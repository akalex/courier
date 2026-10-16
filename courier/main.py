from __future__ import annotations

import asyncio
import logging
import signal

from .adapters import build_waha_transport
from .config import load_config
from .filters import AnyOf, KeywordFilter, SenderFilter
from .forwarder import Forwarder
from .ports import Filter


logger = logging.getLogger(__name__)

TRANSPORTS = {
    "waha": build_waha_transport,
}

FILTER_COMBINER = AnyOf


def build_filter_policy(config: dict) -> Filter | None:
    """Build the configured message filter policy from YAML and environment settings.

    Args:
        config: The normalized application configuration dictionary.

    Returns:
        A filter instance combining keyword and sender filters, or None if no filters
        are configured.

    Raises:
        ValueError: If the configuration contains unsupported filter values.
    """
    keywords = config.get("filters", {}).get("keywords", []) or []
    senders = config.get("filters", {}).get("senders", []) or []

    filters: list[Filter] = []
    if keywords:
        filters.append(KeywordFilter(keywords))
    if senders:
        filters.append(SenderFilter(senders))
    if not filters:
        return None
    return FILTER_COMBINER(*filters)


async def _shutdown(transport, stop_event: asyncio.Event) -> None:
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
        level=getattr(logging, str(config["log_level"]).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    transport_name = config["transport"]
    builder = TRANSPORTS.get(transport_name)
    if builder is None:
        raise ValueError(f"Unknown transport '{transport_name}'. Available: {', '.join(sorted(TRANSPORTS))}")

    transport = builder(config)
    filter_config = config["filters"]
    keywords = filter_config["keywords"]
    senders = filter_config["senders"]
    if keywords or senders:
        logger.info(
            "Message filters: keywords=%s senders=%s (OR)",
            keywords or "disabled",
            senders or "disabled",
        )
    else:
        logger.info("Message filters: none (all messages from the source chat are eligible)")

    forwarder = Forwarder(
        source_chat_id=config["source_chat_id"],
        target_chat_id=config["target_chat_id"],
        transport=transport,
        filter_policy=build_filter_policy(config),
        recent_ids=1000,
        max_attachment_size=20 * 1024 * 1024,
    )

    stop_event = asyncio.Event()

    async def _handle_signal() -> None:
        await _shutdown(transport, stop_event)

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, lambda: asyncio.create_task(_handle_signal()))
        except (NotImplementedError, AttributeError):
            pass

    await transport.start(forwarder.handle_message)
    logger.info(
        "Courier started with source=%s target=%s transport=%s include_from_me=%s",
        config["source_chat_id"],
        config["target_chat_id"],
        transport_name,
        config["include_from_me"],
    )
    await stop_event.wait()


if __name__ == "__main__":
    """Entry point for running Courier as a standalone application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
