from __future__ import annotations

import asyncio
import logging
import signal
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial

from .adapters import WAHA, WAHAWebhookIngress, build_matrix_destination, build_waha
from .config import ConfigError, CourierConfig, FilterSettings, ForwarderConfig, HealthMonitorConfig, load_config
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


def build_filter_policy(filters: FilterSettings) -> Filter:
    """Build the message filter policy from a forwarder's filter settings.

    Args:
        filters: The forwarder's include and exclude criteria.

    Returns:
        A policy that accepts a message matching any include criterion (or all messages when there are none)
        and rejects any message containing an excluded keyword.
    """
    allow_filters: list[Filter] = []
    if filters.keywords:
        allow_filters.append(KeywordFilter(filters.keywords))
    if filters.senders:
        allow_filters.append(SenderFilter(filters.senders))

    allow_policy = FILTER_COMBINER(*allow_filters) if allow_filters else AllOf()
    return AllOf(allow_policy, ExcludeKeywordFilter(filters.exclude_keywords))


def build_route(config: CourierConfig, waha: WAHA) -> Route:
    """Wire the destination, forwarder, health monitor, and webhook ingress for the container's role.

    Args:
        config: The validated container config.
        waha: The WAHA session messages come from.

    Returns:
        The route's components; the caller owns starting and stopping them.
    """
    destination = build_matrix_destination(config.matrix) if config.matrix is not None else waha.destination
    forwarder = _build_forwarder(config, destination) if isinstance(config, ForwarderConfig) else None
    alert_chat_id = _alert_chat_id(config)
    monitor = None
    if alert_chat_id is not None:
        monitor = SessionHealthMonitor(alerts=partial(destination.send, alert_chat_id), probe=waha.probe)
    ingress = WAHAWebhookIngress(
        waha,
        include_from_me=config.include_from_me if isinstance(config, ForwarderConfig) else False,
        on_message=forwarder.handle_message if forwarder is not None else None,
        on_status=monitor.observe if monitor is not None else None,
    )
    return Route(destination=destination, ingress=ingress, monitor=monitor)


def _build_forwarder(config: ForwarderConfig, destination: MessageDestination) -> Forwarder:
    return Forwarder(
        source_chat_id=config.source_chat_id,
        target_chat_id=config.target_chat_id,
        destination=destination,
        filter_policy=build_filter_policy(config.filters),
        max_attachment_size=config.max_attachment_size,
        forward_content=config.forward_content,
        media_types=config.media_types,
        message_transform=config.message_transform.build() if config.message_transform is not None else None,
    )


def _alert_chat_id(config: CourierConfig) -> str | None:
    if isinstance(config, HealthMonitorConfig):
        return config.alert_chat_id
    return config.alert_chat_id if config.health_alerts else None


def _log_startup(config: CourierConfig) -> None:
    if isinstance(config, HealthMonitorConfig):
        logger.info(
            "Courier started as health monitor: session=%s alerts=%s/%s",
            config.waha.session,
            config.destination_transport,
            config.alert_chat_id,
        )
        return
    logger.info(
        "Courier started as forwarder: source=%s/%s target=%s/%s include_from_me=%s health_alerts=%s",
        config.source_transport,
        config.source_chat_id,
        config.destination_transport,
        config.target_chat_id,
        config.include_from_me,
        config.health_alerts,
    )
    filters = config.filters
    if filters.keywords or filters.exclude_keywords or filters.senders:
        logger.info(
            "Message filters: keywords=%s senders=%s (OR), exclude_keywords=%s",
            filters.keywords or "disabled",
            filters.senders or "disabled",
            filters.exclude_keywords or "disabled",
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
        ConfigError: If the config file is missing or invalid.
        httpx.HTTPError: If the WAHA session cannot be configured.
    """
    loaded = load_config()
    config = loaded.config
    _configure_logging(config.log_level)
    stop_event = asyncio.Event()
    _stop_on_signals(stop_event)

    async with AsyncExitStack() as stack:
        waha = build_waha(config.waha, loaded.waha_api_key)
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

        _log_startup(config)
        await stop_event.wait()
        logger.info("Shutting down courier")


if __name__ == "__main__":
    """Entry point for running Courier as a standalone application."""
    try:
        asyncio.run(main())
    except ConfigError as error:
        print(f"Courier cannot start until these config problems are fixed:\n{error}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass
