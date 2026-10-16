from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

from .adapters import build_waha_transport
from .config import AppConfig, load_config
from .filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from .forwarder import Forwarder
from .ports import Filter, Transport


logger = logging.getLogger(__name__)

TRANSPORTS: dict[str, Callable[[AppConfig], Transport]] = {
    "waha": build_waha_transport,
}

FILTER_COMBINER = AnyOf


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


async def _shutdown(transport: Transport, stop_event: asyncio.Event) -> None:
    """Stop a transport and signal the server loop to exit cleanly.

    Args:
        transport: The transport instance to shut down.
        stop_event: Event set after the shutdown sequence completes.

    Raises:
        Exception: Any transport shutdown failure is logged and still propagated.
    """
    logger.info("Shutting down courier")
    try:
        await transport.stop()
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

    transport_name = config.transport
    builder = TRANSPORTS.get(transport_name)
    if builder is None:
        raise ValueError(f"Unknown transport '{transport_name}'. Available: {', '.join(sorted(TRANSPORTS))}")

    transport = builder(config)
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
        transport=transport,
        filter_policy=build_filter_policy(config),
        recent_ids=1000,
        max_attachment_size=config.max_attachment_size,
    )

    stop_event = asyncio.Event()

    async def _handle_signal() -> None:
        await _shutdown(transport, stop_event)

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, lambda: asyncio.create_task(_handle_signal()))
        except NotImplementedError, AttributeError:
            pass

    await transport.start(forwarder.handle_message)
    logger.info(
        "Courier started with source=%s target=%s transport=%s include_from_me=%s",
        config.source_chat_id,
        config.target_chat_id,
        transport_name,
        config.include_from_me,
    )
    await stop_event.wait()


if __name__ == "__main__":
    """Entry point for running Courier as a standalone application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
