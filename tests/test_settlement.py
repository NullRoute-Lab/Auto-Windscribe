import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from auto_windscribe.models import Outcome, Result, State, Status
from auto_windscribe.recovery import NetworkRecoveryManager
from auto_windscribe.storage import Store
from tests.fakes import FakeVPN
from tests.test_engine import catalog, make_controller
from tests.test_recovery import health


def fast(profile):
    return replace(
        profile,
        settlement_timeout=0.5,
        settlement_poll=0.01,
        settlement_poll_max=0.02,
        recovery_delay=0.1,
        recovery_timeout=1,
    )


async def test_late_native_settlement_not_limited_by_two_observations(profile):
    vpn = FakeVPN()
    states = [Status(State.CONNECTING, firewall="on")] * 8

    async def status():
        return states.pop(0) if states else Status(State.DISCONNECTED, firewall="unknown")

    vpn.status = status
    disconnect = AsyncMock()
    manager = NetworkRecoveryManager(
        vpn, AsyncMock(check=AsyncMock(return_value=health())), disconnect
    )
    phases = []
    manager.emit = lambda r: phases.append(r.phase)
    result = await manager.recover(fast(profile))
    assert result.safe and result.settlement_resolved and result.checks == 1
    assert result.timeline[0]["firewall"] == "on"
    assert result.timeline[-1]["native"] == "disconnected"
    assert phases.count("NATIVE_SETTLING") > 2
    assert phases[-1] == "RECOVERED"
    disconnect.assert_not_awaited()


async def test_endless_negotiation_exhausts_deadline_not_check_count(profile):
    vpn = FakeVPN()
    vpn.current = Status(State.CONNECTING)
    manager = NetworkRecoveryManager(vpn, AsyncMock(), AsyncMock())
    r = await manager.recover(replace(fast(profile), settlement_timeout=0.1))
    assert r.phase == "RECOVERY_EXHAUSTED" and not r.safe and r.checks == 0
    assert not vpn.calls


async def test_firewall_change_during_baseline_restarts_settlement(profile):
    vpn = FakeVPN()
    check = AsyncMock(return_value=health())
    count = 0

    async def status():
        nonlocal count
        count += 1
        return Status(State.DISCONNECTED, firewall="on" if count > 3 else "unknown")

    vpn.status = status
    r = await NetworkRecoveryManager(vpn, AsyncMock(check=check), AsyncMock()).recover(
        fast(profile)
    )
    assert not r.safe  # healthy report does not match the changing native firewall evidence
    assert len(r.timeline) >= 2


async def test_unknown_recovered_continues_without_rewriting_history(
    candidate, profile, store, paths
):
    vpn = FakeVPN()
    engine = make_controller(store, paths, vpn)
    engine.recovery = NetworkRecoveryManager(
        vpn, AsyncMock(check=AsyncMock(return_value=health())), engine._disconnect
    )

    async def attempt(c, p):
        vpn.calls.append(("connect", c.key))
        return Result(
            c,
            Outcome.UNKNOWN,
            "connection_timeout",
            "Synthetic",
            context=p.context,
            phase="establishment",
            observations=["connecting"],
        )

    engine._attempt = attempt
    p = replace(fast(profile), max_tests=2)
    other = replace(candidate, port=80)
    cat = catalog(candidate)
    cat.capabilities["stealth"].append(80)
    scan = store.new_scan([candidate, other], p)
    await engine.scan(scan, cat)
    assert store.manifest(scan)["completed"] == 2
    assert store.manifest(scan)["unknown"] == 2
    assert store.manifest(scan)["sessions"][0]["attempts"] == 2
    assert all(r.outcome == Outcome.UNKNOWN for r in store.history())
    assert store.recovery_status()["settlement_resolved"]
    assert len(vpn.calls) == 2


def test_full_inventory_manifest_survives_restart_over_display_limit(candidate, profile, paths):
    queue = [
        replace(candidate, location=replace(candidate.location, nickname=f"Example {i}"))
        for i in range(1205)
    ]
    db = Store(paths.data / "full.sqlite3")
    scan = db.new_scan(queue, replace(profile, strategy="exhaustive"))
    for position, c in enumerate(queue[:1001]):
        db.record(Result(c, Outcome.FAILED, "failure", "Synthetic"), scan, position)
    db.close()
    db = Store(paths.data / "full.sqlite3")
    manifest = db.manifest(scan)
    assert manifest["total"] == 1205 and manifest["remaining"] == 204
    assert manifest["failed"] == 1001 and manifest["tested"] == 1001
    assert db.pending(scan)[0][0] == 1001
    db.close()


async def test_cancel_during_settlement_preserves_original_observation(
    candidate, profile, store, paths
):
    vpn = FakeVPN()
    engine = make_controller(store, paths, vpn)
    started = asyncio.Event()

    async def attempt(c, p):
        return Result(
            c, Outcome.UNKNOWN, "connection_timeout", "Synthetic", observations=["connecting"]
        )

    engine._attempt = attempt
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)

    async def recover(*a, **kw):
        started.set()
        await asyncio.sleep(10)

    engine.recovery.recover = recover
    scan = store.new_scan([candidate], profile)
    task = asyncio.create_task(engine.scan(scan, catalog(candidate)))
    await started.wait()
    engine.stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(store.history()) == 1 and store.history()[0].code == "connection_timeout"
    assert store.manifest(scan)["remaining"] == 1


async def test_exhaustive_executes_after_thousandth_checkpoint(candidate, profile, store, paths):
    locations = [replace(candidate.location, nickname=f"Entry {i}") for i in range(1002)]
    queue = [replace(candidate, location=loc) for loc in locations]
    scan = store.new_scan(queue, replace(profile, strategy="exhaustive", max_tests=1))
    for pos, c in enumerate(queue[:1001]):
        store.record(Result(c, Outcome.FAILED, "failed", "Synthetic"), scan, pos)
    cat = catalog(candidate)
    cat.locations = locations
    engine = make_controller(store, paths)
    await engine.scan(scan, cat)
    assert engine.vpn.calls == [("connect", queue[-1].key)]
    assert store.manifest(scan)["remaining"] == 0
    assert store.manifest(scan)["total"] == 1002


def test_crash_reservation_survives_restart(candidate, profile, paths):
    path = paths.data / "crash.sqlite3"
    store = Store(path)
    scan = store.new_scan([candidate], profile)
    store.reserve(scan, candidate)
    store.close()
    store = Store(path)
    assert store.previous(scan, candidate).phase == "dispatch"
    assert len(store.pending(scan)) == 1
    store.close()
