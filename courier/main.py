from __future__ import annotations

import asyncio
import logging
import signal
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial

from .adapters import WAHA, WAHAWebhookIngress, build_matrix_destination, build_waha
from .config import AppConfig, load_config
from .core.filters import AllOf, AnyOf, ExcludeKeywordFilter, KeywordFilter, SenderFilter
from .core.forwarder import Forwarder
from .core.health import SessionHealthMonitor
from .core.ports import Filter, MessageDestination
from .infrastructure.logging_context import CourierLogContextFilter
from .infrastructure.webhook_server import WebhookServer


logger = logging.getLogger(__name__)

FILTER_COMBINER = AnyOf


@dataclass(frozen=True)
class Route:
    """One Courier process: where messages are sent, what handles webhooks, and optional health alerts."""

    destination: MessageDestination
    ingress: WAHAWebhookIngress
    monitor: SessionHealthMonitor | None


def _configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(courier_context)s%(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(CourierLogContextFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)


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


def build_route(config: AppConfig, waha: WAHA) -> Route:
    """Wire the destination, forwarder, health monitor, and webhook ingress for the configured route.

    Args:
        config: The validated application configuration.
        waha: The WAHA session messages come from.

    Returns:
        The route's components; the caller owns starting and stopping them.
    """
    destination: MessageDestination = (
        waha.destination if config.destination_transport == "waha" else build_matrix_destination(config)
    )
    forwarder = None
    if not config.health_monitor_only:
        forwarder = Forwarder(
            source_chat_id=config.source_chat_id,
            target_chat_id=config.target_chat_id,
            destination=destination,
            filter_policy=build_filter_policy(config),
            max_attachment_size=config.max_attachment_size,
            forward_content=config.forward_content,
            media_types=config.media_types,
        )
    monitor = None
    if config.health_alerts_enabled:
        monitor = SessionHealthMonitor(alerts=partial(destination.send, config.alert_chat_id), probe=waha.probe)
    ingress = WAHAWebhookIngress(
        waha,
        include_from_me=config.include_from_me,
        on_message=forwarder.handle_message if forwarder is not None else None,
        on_status=monitor.observe if monitor is not None else None,
    )
    return Route(destination=destination, ingress=ingress, monitor=monitor)


def _log_filters(config: AppConfig) -> None:
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


def _stop_on_signals(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError, AttributeError:
            pass


async def main() -> None:
    """Run the Courier process with the configured route until SIGINT or SIGTERM.

    Raises:
        ValueError: If the configuration is invalid.
        httpx.HTTPError: If the WAHA session cannot be configured.
    """
    config = load_config()
    _configure_logging(config.log_level)
    _log_filters(config)
    stop_event = asyncio.Event()
    _stop_on_signals(stop_event)

    async with AsyncExitStack() as stack:
        waha = build_waha(config)
        stack.push_async_callback(waha.aclose)
        route = build_route(config, waha)
        stack.push_async_callback(route.destination.stop)
        server = WebhookServer(host="0.0.0.0", port=config.listen_port, handler=route.ingress.handle)
        await server.start()
        stack.push_async_callback(server.stop)
        await waha.provision(config.webhook_url, route.ingress.events)
        if route.monitor is not None:
            await route.monitor.start()
            stack.push_async_callback(route.monitor.stop)

        logger.info(
            "Courier started with source=%s/%s target=%s/%s include_from_me=%s health_monitor_only=%s",
            config.source_transport,
            config.source_chat_id,
            config.destination_transport,
            config.target_chat_id,
            config.include_from_me,
            config.health_monitor_only,
        )
        await stop_event.wait()
        logger.info("Shutting down courier")


if __name__ == "__main__":
    """Entry point for running Courier as a standalone application."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
