from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

from .adapters import WAHATransport, build_matrix_destination, build_waha_transport
from .config import AppConfig, load_config
from .domain import Message
from .filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from .forwarder import Forwarder
from .ports import Filter, MessageDestination, MessageSource


logger = logging.getLogger(__name__)

SOURCES: dict[str, Callable[[AppConfig], MessageSource]] = {
    "waha": build_waha_transport,
}
DESTINATIONS: dict[str, Callable[[AppConfig], MessageDestination]] = {
    "matrix": build_matrix_destination,
    "waha": build_waha_transport,
}

FILTER_COMBINER = AnyOf


def _build_route(config: AppConfig) -> tuple[MessageSource, MessageDestination]:
    source_builder = SOURCES.get(config.source_transport)
    if source_builder is None:
        available = ", ".join(sorted(SOURCES))
        raise ValueError(f"Unknown source transport '{config.source_transport}'. Available: {available}")
    destination_builder = DESTINATIONS.get(config.destination_transport)
    if destination_builder is None:
        available = ", ".join(sorted(DESTINATIONS))
        raise ValueError(f"Unknown destination transport '{config.destination_transport}'. Available: {available}")

    source = source_builder(config)
    destination: MessageDestination
    if config.destination_transport == "waha":
        if not isinstance(source, MessageDestination):
            raise ValueError(f"Source transport '{config.source_transport}' cannot also be used as a WAHA destination")
        destination = source
    else:
        destination = destination_builder(config)
    return source, destination


def build_filter_policy(config: AppConfig) -> Filter | None:
    """Build the configured message filter policy from YAML and environment settings.

    Args:
        config: The validated application configuration.

    Returns:
        A filter policy combining positive criteria and excluded keywords.

    Raises:
        ValueError: If the configuration contains unsupported filter values.
    """
    keywords = config.filters.keywords
    senders = config.filters.senders

    allow_filters: list[Filter] = []
    if keywords:
        allow_filters.append(KeywordFilter(keywords))
    if senders:
        allow_filters.append(SenderFilter(senders))

    allow_policy = FILTER_COMBINER(*allow_filters) if allow_filters else AllOf()
    exclude_policy = ExcludeKeywordFilter(config.filters.exclude_keywords)
    return AllOf(allow_policy, exclude_policy)


async def _ignore_message(message: Message) -> None:
    logger.debug("Health monitor ignored message id=%s", message.id)


async def _start_source(source: MessageSource, forwarder: Forwarder, *, health_monitor_only: bool) -> None:
    message_handler = _ignore_message if health_monitor_only else forwarder.handle_message
    await source.start(message_handler)


async def _shutdown(
    source: MessageSource,
    destination: MessageDestination,
    stop_event: asyncio.Event,
) -> None:
    """Stop route components and signal the server loop to exit cleanly.

    Args:
        source: Messaging source to shut down.
        destination: Messaging destination to shut down when it is a separate instance.
        stop_event: Event set after the shutdown sequence completes.

    Raises:
        Exception: Any shutdown failure is propagated after attempting both components.
    """
    logger.info("Shutting down courier")
    try:
        if destination is not source:
            try:
                await destination.stop()
            finally:
                await source.stop()
        else:
            await source.stop()
    finally:
        stop_event.set()


async def main() -> None:
    """Run the Courier process with the configured transport and forwarding policy.

    Returns:
        None.

    Raises:
        ValueError: If the transport name is unknown or required config values are missing.
        KeyboardInterrupt: If the process is interrupted while running.
    """
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    source, destination = _build_route(config)
    keywords = config.filters.keywords
    exclude_keywords = config.filters.exclude_keywords
    senders = config.filters.senders
    if keywords or exclude_keywords or senders:
        logger.info(
            "Message filters: keywords=%s senders=%s (OR), exclude_keywords=%s",
            keywords or "disabled",
            senders or "disabled",
            exclude_keywords or "disabled",
        )
    else:
        logger.info("Message filters: none (all messages from the source chat are eligible)")

    forwarder = Forwarder(
        source_chat_id=config.source_chat_id,
        target_chat_id=config.target_chat_id,
        destination=destination,
        filter_policy=build_filter_policy(config),
        recent_ids=1000,
        max_attachment_size=config.max_attachment_size,
        forward_content=config.forward_content,
        media_types=config.media_types,
    )

    stop_event = asyncio.Event()

    if isinstance(source, WAHATransport) and config.health_alerts_enabled:
        source.set_alert_handler(lambda text: destination.send(config.alert_chat_id, text))

    async def _handle_signal() -> None:
        await _shutdown(source, destination, stop_event)

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, lambda: asyncio.create_task(_handle_signal()))
        except NotImplementedError, AttributeError:
            pass

    try:
        await _start_source(source, forwarder, health_monitor_only=config.health_monitor_only)
    except Exception:
        if destination is not source:
            try:
                await destination.stop()
            except Exception:
                logger.exception("Failed to stop destination after source startup failure")
        raise
    logger.info(
        "Courier started with source=%s/%s target=%s/%s include_from_me=%s",
        config.source_transport,
        config.source_chat_id,
        config.destination_transport,
        config.target_chat_id,
        config.include_from_me,
    )
    await stop_event.wait()


if __name__ == "__main__":
    """Entry point for running Courier as a standalone application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
