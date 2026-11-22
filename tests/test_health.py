import asyncio
import logging

import pytest

from courier.core.health import HealthObservation, SessionHealthMonitor


STOPPED = HealthObservation(issue="session:unhealthy", text="WAHA session demo is STOPPED.")
WORKER_DOWN = HealthObservation(
    issue="worker:unavailable",
    text="WAHA worker returned HTTP 503 while checking session demo.",
)
HEALTHY = HealthObservation(issue=None, text="Recovery update: WAHA session demo and worker are back online.")


class AlertLog:
    def __init__(self, *, fail=False):
        self.texts = []
        self.fail = fail

    async def __call__(self, text):
        self.texts.append(text)
        if self.fail:
            raise RuntimeError("alert chat unavailable")


class ManualSleep:
    """Fake sleep that parks the poll loop until the test advances it by one round."""

    def __init__(self):
        self.delays = []
        self.parked = asyncio.Event()
        self._release = None

    async def __call__(self, seconds):
        self.delays.append(seconds)
        self._release = asyncio.get_running_loop().create_future()
        self.parked.set()
        await self._release

    async def advance(self):
        await self.parked.wait()
        self.parked.clear()
        self._release.set_result(None)
        await self.parked.wait()


class ScriptedProbe:
    def __init__(self, *results):
        self.results = list(results)

    async def check(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


async def test_alerts_once_per_distinct_issue_and_once_on_recovery():
    alerts = AlertLog()
    monitor = SessionHealthMonitor(alerts=alerts)

    for observation in (STOPPED, STOPPED, WORKER_DOWN, HEALTHY, HEALTHY):
        await monitor.observe(observation)

    assert alerts.texts == [
        "[Courier Bot] WAHA session demo is STOPPED.",
        "[Courier Bot] WAHA worker returned HTTP 503 while checking session demo.",
        "[Courier Bot] Recovery update: WAHA session demo and worker are back online.",
    ]


async def test_healthy_observation_without_prior_issue_sends_nothing():
    alerts = AlertLog()
    monitor = SessionHealthMonitor(alerts=alerts)

    await monitor.observe(HEALTHY)

    assert alerts.texts == []


async def test_alert_delivery_failure_is_logged_not_raised(caplog):
    monitor = SessionHealthMonitor(alerts=AlertLog(fail=True))

    with caplog.at_level(logging.ERROR, logger="courier.core.health"):
        await monitor.observe(STOPPED)

    assert "Failed to deliver health alert" in caplog.text


async def test_poll_loop_reports_probe_results_every_interval():
    alerts = AlertLog()
    sleep = ManualSleep()
    monitor = SessionHealthMonitor(
        alerts=alerts,
        probe=ScriptedProbe(WORKER_DOWN, HEALTHY),
        interval=30.0,
        sleep=sleep,
    )

    await monitor.start()
    await sleep.advance()
    await sleep.advance()
    await monitor.stop()

    assert sleep.delays[:2] == [30.0, 30.0]
    assert alerts.texts == [
        "[Courier Bot] WAHA worker returned HTTP 503 while checking session demo.",
        "[Courier Bot] Recovery update: WAHA session demo and worker are back online.",
    ]


async def test_poll_loop_survives_inconclusive_and_failing_probes():
    alerts = AlertLog()
    sleep = ManualSleep()
    monitor = SessionHealthMonitor(
        alerts=alerts,
        probe=ScriptedProbe(None, RuntimeError("probe bug"), WORKER_DOWN),
        sleep=sleep,
    )

    await monitor.start()
    for _ in range(3):
        await sleep.advance()
    await monitor.stop()

    assert alerts.texts == ["[Courier Bot] WAHA worker returned HTTP 503 while checking session demo."]


async def test_stop_cancels_the_poll_loop():
    sleep = ManualSleep()
    probe = ScriptedProbe(WORKER_DOWN)
    monitor = SessionHealthMonitor(alerts=AlertLog(), probe=probe, sleep=sleep)

    await monitor.start()
    await sleep.parked.wait()
    await monitor.stop()

    assert [task for task in asyncio.all_tasks() if task is not asyncio.current_task()] == []
    assert probe.results == [WORKER_DOWN]


async def test_monitor_without_probe_does_not_poll():
    sleep = ManualSleep()
    monitor = SessionHealthMonitor(alerts=AlertLog(), sleep=sleep)

    await monitor.start()
    await asyncio.sleep(0)
    await monitor.stop()

    assert sleep.delays == []


@pytest.mark.parametrize("observation", [STOPPED, WORKER_DOWN])
async def test_pushed_and_polled_observations_share_one_alert_state(observation):
    alerts = AlertLog()
    sleep = ManualSleep()
    monitor = SessionHealthMonitor(alerts=alerts, probe=ScriptedProbe(observation), sleep=sleep)

    await monitor.observe(observation)
    await monitor.start()
    await sleep.advance()
    await monitor.stop()

    assert alerts.texts == [f"[Courier Bot] {observation.text}"]
