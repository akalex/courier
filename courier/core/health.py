from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol


logger = logging.getLogger(__name__)

AlertSender = Callable[[str], Awaitable[None]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class HealthObservation:
    """One reading of a monitored service's health.

    Attributes:
        issue: Stable code of the current problem, or None when the service is healthy.
        text: Alert text for the issue, or the recovery text when healthy.
    """

    issue: str | None
    text: str


class HealthProbe(Protocol):
    async def check(self) -> HealthObservation | None:
        """Probe the service once; None means the result is inconclusive."""
        ...


class SessionHealthMonitor:
    """Send one alert per distinct health issue and one recovery alert once the issue clears.

    Observations pushed through observe() and those polled from the probe share one state.
    """

    def __init__(
        self,
        *,
        alerts: AlertSender,
        probe: HealthProbe | None = None,
        interval: float = 30.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._alerts = alerts
        self._probe = probe
        self._interval = interval
        self._sleep = sleep
        self._active_issue: str | None = None
        self._poll_task: asyncio.Task[None] | None = None

    async def observe(self, observation: HealthObservation) -> None:
        if observation.issue == self._active_issue:
            return
        self._active_issue = observation.issue
        alert = f"[Courier Bot] {observation.text}"
        logger.warning("%s", alert)
        try:
            await self._alerts(alert)
        except Exception:
            logger.exception("Failed to deliver health alert")

    async def start(self) -> None:
        if self._probe is not None and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._poll(self._probe))

    async def stop(self) -> None:
        if self._poll_task is None:
            return
        self._poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._poll_task
        self._poll_task = None

    async def _poll(self, probe: HealthProbe) -> None:
        while True:
            await self._sleep(self._interval)
            try:
                observation = await probe.check()
            except Exception:
                logger.exception("Health probe failed")
                continue
            if observation is not None:
                await self.observe(observation)
