import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from auto_windscribe.demo import DemoGate
from auto_windscribe.errors import AppError
from auto_windscribe.health import HealthState, NetworkHealthChecker, classify
from auto_windscribe.models import State, Status
from auto_windscribe.process import Completed
from auto_windscribe.recovery import GUICompatibility, NetworkRecoveryManager
from tests.fakes import FakeVPN
from tests.test_engine import catalog, make_controller


@pytest.mark.parametrize(
    "dns,ping,google,other,expected,safe",
    [
        (True, True, True, True, "HEALTHY", True),
        (True, False, True, True, "DEGRADED", True),
        (False, False, False, True, "DNS_FAILURE", False),
        (True, True, False, False, "HTTPS_UNAVAILABLE", False),
        (False, False, False, False, "NO_CONNECTIVITY", False),
        (None, None, None, None, "UNKNOWN", False),
        (True, False, False, True, "DEGRADED", False),
    ],
)
def test_evidence_classification(dns, ping, google, other, expected, safe):
    r = classify(Status(State.DISCONNECTED), dns, ping, google, other, "present")
    assert r.state == expected and r.safe == safe


@pytest.mark.parametrize("firewall", ["On", "Always On", "Off"])
def test_firewall_state_is_not_causal_proof(firewall):
    r = classify(
        Status(State.DISCONNECTED, firewall=firewall), False, False, False, False, "present"
    )
    assert r.state == HealthState.NO_CONNECTIVITY and r.firewall == firewall
    if firewall != "Off":
        assert "policy" in r.detail


async def test_exact_health_commands_and_redaction(profile):
    calls = []

    async def run(argv, timeout=10):  # noqa: ASYNC109
        calls.append(argv)
        if argv[0].endswith("ip"):
            return Completed(0, '[{"gateway":"sensitive", "dev":"private-device"}]')
        return Completed(0, "204" if argv[0].endswith("curl") else "private resolver output")

    runner = AsyncMock(run=AsyncMock(side_effect=run))
    report = await NetworkHealthChecker(runner, FakeVPN(), DemoGate()).check(profile)
    assert report.safe and "private" not in str(report) and "sensitive" not in str(report)
    assert ["/usr/bin/ping", "-n", "-c", "2", "-W", "2", "google.com"] in calls
    assert ["/usr/bin/getent", "ahosts", "google.com"] in calls
    assert ["/usr/sbin/ip", "-j", "route", "show", "default"] in calls
    assert len(calls) == 5
    for argv in calls:
        assert "--interface" not in argv and "sudo" not in argv


async def test_health_cancel_cleans_all_probes(profile):
    active = 0

    async def run(*args, **kw):
        nonlocal active
        active += 1
        try:
            await asyncio.sleep(10)
        finally:
            active -= 1

    checker = NetworkHealthChecker(AsyncMock(run=AsyncMock(side_effect=run)), FakeVPN(), DemoGate())
    task = asyncio.create_task(checker.check(profile))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert active == 0


def health(ok=True):
    return classify(Status(State.DISCONNECTED), ok, ok, ok, ok, "present")


@pytest.mark.parametrize(
    "outcomes,expected,count",
    [([True], True, 1), ([False, True], True, 2), ([False, False], False, 2)],
)
async def test_bounded_recovery_checks(profile, outcomes, expected, count):
    checker = AsyncMock(check=AsyncMock(side_effect=[health(v) for v in outcomes]))
    disconnect = AsyncMock()
    manager = NetworkRecoveryManager(FakeVPN(), checker, disconnect)
    result = await manager.recover(
        replace(
            profile,
            recovery_delay=0.1,
            settlement_timeout=0.5,
            settlement_poll=0.01,
            settlement_poll_max=0.02,
        )
    )
    assert result.safe == expected and result.checks == count
    disconnect.assert_not_awaited()


@pytest.mark.parametrize("state", [State.CONNECTING, State.UNKNOWN, State.DISCONNECTING])
async def test_uncertain_state_never_dispatches(profile, state):
    vpn = FakeVPN()
    vpn.current = Status(state)
    checker, disconnect = AsyncMock(), AsyncMock()
    result = await NetworkRecoveryManager(vpn, checker, disconnect).recover(
        replace(
            profile,
            recovery_delay=0.1,
            settlement_timeout=0.5,
            settlement_poll=0.01,
            settlement_poll_max=0.02,
        )
    )
    assert not result.safe and not vpn.calls
    checker.check.assert_not_awaited()
    disconnect.assert_not_awaited()


async def test_delayed_connection_is_unowned(profile, candidate):
    vpn = FakeVPN()
    vpn.current = Status(
        State.CONNECTED, candidate.location.city, candidate.protocol, candidate.port
    )
    disconnect = AsyncMock()
    result = await NetworkRecoveryManager(vpn, AsyncMock(), disconnect).recover(
        profile, candidate, unresolved=True
    )
    assert not result.safe
    disconnect.assert_not_awaited()


@pytest.mark.parametrize("change", ["city", "protocol"])
async def test_gui_conflict_is_protected(profile, candidate, change):
    vpn = FakeVPN()
    vpn.current = Status(
        State.CONNECTED,
        "Other" if change == "city" else candidate.location.city,
        "udp" if change == "protocol" else candidate.protocol,
        candidate.port,
    )
    disconnect = AsyncMock()
    result = await NetworkRecoveryManager(vpn, AsyncMock(), disconnect).recover(
        profile, candidate, owned=True
    )
    assert not result.safe
    disconnect.assert_not_awaited()
    assert "Switch to Auto" in GUICompatibility.warning


async def test_owned_tunnel_graceful_disconnect_only_once(profile, candidate):
    vpn = FakeVPN()
    vpn.current = Status(
        State.CONNECTED, candidate.location.city, candidate.protocol, candidate.port
    )

    async def disconnect(c):
        assert c == candidate
        await vpn.disconnect()

    manager = NetworkRecoveryManager(
        vpn, AsyncMock(check=AsyncMock(return_value=health())), disconnect
    )
    assert (
        await manager.recover(
            replace(
                profile,
                recovery_delay=0.1,
                settlement_timeout=0.5,
                settlement_poll=0.01,
                settlement_poll_max=0.02,
            ),
            candidate,
            owned=True,
        )
    ).safe
    assert vpn.calls == [("disconnect",)]


@pytest.mark.parametrize("restored", [True, False])
async def test_scan_continues_only_after_recovery(profile, candidate, store, paths, restored):
    vpn = FakeVPN()
    vpn.on_connect = lambda c: setattr(
        vpn, "current", Status(State.DISCONNECTED, logged_in=True, diagnostic="tunnel_failed")
    )
    engine = make_controller(store, paths, vpn)
    checker = AsyncMock(check=AsyncMock(return_value=health(restored)))
    engine.recovery = NetworkRecoveryManager(vpn, checker, engine._disconnect)
    p = replace(profile, max_tests=2, recovery_delay=0.1)
    second = replace(candidate, port=80)
    cat = catalog(candidate)
    cat.capabilities["stealth"].append(80)
    scan = store.new_scan([candidate, second], p)
    await engine.scan(scan, cat)
    assert len(vpn.calls) == (2 if restored else 1)
    assert store.scan(scan)["state"] == ("complete" if restored else "paused")
    assert len(store.pending(scan)) == (0 if restored else 1)
    assert store.recovery_status()["safe"] == restored


async def test_health_guard_before_commands(profile):
    from auto_windscribe.safety import SafetyGate

    runner = AsyncMock()
    with pytest.raises(AppError):
        await NetworkHealthChecker(runner, FakeVPN(), SafetyGate(confirmed=True)).check(profile)
    runner.run.assert_not_awaited()


async def test_unhealthy_precheck_prevents_connection(profile, candidate, store, paths):
    engine = make_controller(store, paths)
    engine.health = AsyncMock(check=AsyncMock(return_value=health(False)))
    with pytest.raises(AppError, match="Baseline"):
        await engine.connect(candidate, profile)
    assert engine.vpn.calls == []


async def test_unacknowledged_ipc_does_not_become_safe_by_waiting(profile):
    checker = AsyncMock(check=AsyncMock(return_value=health()))
    result = await NetworkRecoveryManager(FakeVPN(), checker, AsyncMock()).recover(
        profile, unresolved=True
    )
    assert not result.safe
    checker.check.assert_not_awaited()


async def test_settling_connection_can_return_to_baseline(profile):
    vpn = FakeVPN()
    states = [Status(State.CONNECTING)] * 4

    async def status():
        return states.pop(0) if states else Status(State.DISCONNECTED)

    vpn.status = status
    disconnect = AsyncMock()
    report = await NetworkRecoveryManager(
        vpn, AsyncMock(check=AsyncMock(return_value=health())), disconnect
    ).recover(
        replace(
            profile,
            recovery_delay=0.1,
            settlement_timeout=0.5,
            settlement_poll=0.01,
            settlement_poll_max=0.02,
        )
    )
    assert report.safe and report.checks == 1
    disconnect.assert_not_awaited()
