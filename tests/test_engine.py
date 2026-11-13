import asyncio
import time
from dataclasses import replace

import pytest

from auto_windscribe.demo import DemoGate
from auto_windscribe.engine import Controller, owns
from auto_windscribe.errors import SafetyError
from auto_windscribe.models import Metrics, Outcome, State, Status
from auto_windscribe.safety import OperationLock, SafetyGate
from auto_windscribe.storage import Catalog
from tests.fakes import FakeProbe, FakeVPN


def make_controller(store, paths, vpn=None, probe=None, gate=None):
    return Controller(
        vpn or FakeVPN(),
        probe or FakeProbe(),
        store,
        OperationLock(paths.data / "operation.lock"),
        gate or DemoGate(),
        poll_interval=0.01,
    )


def catalog(candidate):
    return Catalog([candidate.location], {candidate.protocol: [candidate.port]}, time.time())


async def test_verified_connection_retained(candidate, profile, store, paths):
    engine = make_controller(store, paths)
    result = await engine.connect(candidate, profile)
    assert result.outcome == Outcome.SUCCESS
    assert result.connect_s is not None and result.connect_s >= 0
    assert result.metrics.https_ok
    assert engine.vpn.calls == [("connect", candidate.key)]
    assert store.history()[0].outcome == Outcome.SUCCESS


async def test_icmp_blocking_does_not_fail_https(candidate, profile, store, paths):
    probe = FakeProbe(Metrics(loss_pct=100, https_ok=True))
    result = await make_controller(store, paths, probe=probe).connect(candidate, profile)
    assert result.outcome == Outcome.SUCCESS
    assert result.metrics.rtt_ms is None


async def test_exit_zero_without_tunnel_is_failure(candidate, profile, store, paths):
    vpn = FakeVPN()
    vpn.on_connect = lambda c: setattr(
        vpn, "current", Status(State.DISCONNECTED, logged_in=True, diagnostic="tunnel_failed")
    )
    probe = FakeProbe()
    result = await make_controller(store, paths, vpn, probe).connect(candidate, profile)
    assert result.outcome == Outcome.FAILED
    assert result.connect_s is None
    assert probe.calls == 0


async def test_existing_connection_never_disconnected(candidate, profile, store, paths):
    vpn = FakeVPN()
    vpn.current = Status(State.CONNECTED, "Other City", "udp", 80, logged_in=True)
    engine = make_controller(store, paths, vpn)
    with pytest.raises(SafetyError, match="not disconnected"):
        await engine.connect(candidate, profile)
    assert not vpn.calls
    scan_id = store.new_scan([candidate], profile)
    with pytest.raises(SafetyError):
        await engine.scan(scan_id, catalog(candidate))
    assert not vpn.calls


async def test_explicit_interrupt(candidate, profile, store, paths):
    vpn = FakeVPN()
    vpn.current = Status(State.CONNECTED, "Other City", "udp", 80, logged_in=True)
    await make_controller(store, paths, vpn).connect(candidate, profile, interrupt=True)
    assert vpn.calls[0] == ("disconnect",)
    assert vpn.calls[1][0] == "connect"


async def test_connection_timeout_leaves_state_and_pauses(candidate, profile, store, paths):
    vpn = FakeVPN(delay=10)
    engine = make_controller(store, paths, vpn)
    scan_id = store.new_scan([candidate], profile)
    await engine.scan(scan_id, catalog(candidate))
    assert store.scan(scan_id)["state"] == "paused"
    assert store.history()[0].code == "connection_timeout"
    assert store.history()[0].outcome == Outcome.UNKNOWN
    assert len(store.pending(scan_id)) == 1
    assert all(call[0] != "disconnect" for call in vpn.calls)


async def test_cancel_scan_resumes_pending_without_false_failure(candidate, profile, store, paths):
    vpn = FakeVPN(delay=10)
    engine = make_controller(store, paths, vpn)
    scan_id = store.new_scan([candidate], profile)
    task = asyncio.create_task(engine.scan(scan_id, catalog(candidate)))
    await asyncio.wait_for(vpn.started.wait(), 1)
    engine.stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.scan(scan_id)["state"] == "paused"
    assert store.history()[0].outcome == Outcome.CANCELLED
    assert len(store.pending(scan_id)) == 1
    assert not any(c[0] == "disconnect" for c in vpn.calls)
    vpn.delay = 0
    await engine.scan(scan_id, catalog(candidate))
    assert store.scan(scan_id)["state"] == "succeeded"
    assert len(store.pending(scan_id)) == 0


async def test_stop_and_quit_do_not_disconnect_established_tunnel(candidate, profile, store, paths):
    vpn, probe = FakeVPN(), FakeProbe(delay=10)
    engine = make_controller(store, paths, vpn, probe)
    task = asyncio.create_task(engine.connect(candidate, profile))
    await asyncio.wait_for(probe.started.wait(), 1)
    engine.stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert vpn.current.state == State.CONNECTED
    assert probe.cancelled
    assert not any(c[0] == "disconnect" for c in vpn.calls)


async def test_concurrent_operations_refused(candidate, profile, store, paths):
    engine = make_controller(store, paths, probe=FakeProbe(delay=10))
    task = asyncio.create_task(engine.connect(candidate, profile))
    await asyncio.wait_for(engine.vpn.started.wait(), 1)
    with pytest.raises(SafetyError, match="already running"):
        await engine.disconnect()
    engine.stop()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_external_change_cancels_probes(candidate, profile, store, paths):
    vpn, probe = FakeVPN(), FakeProbe(delay=10)
    engine = make_controller(store, paths, vpn, probe)
    scan_id = store.new_scan([candidate], profile)
    task = asyncio.create_task(engine.scan(scan_id, catalog(candidate)))
    await asyncio.wait_for(probe.started.wait(), 1)
    vpn.current = Status(State.CONNECTED, "Unrelated City", "tcp", 80, logged_in=True)
    with pytest.raises(SafetyError, match="changed"):
        await task
    assert probe.cancelled
    assert store.scan(scan_id)["state"] == "paused"
    assert store.history()[0].outcome == Outcome.UNKNOWN
    assert store.history()[0].metrics.rtt_ms is None
    assert not any(c[0] == "disconnect" for c in vpn.calls)


async def test_rank_scan_disconnects_only_between_owned_connections(
    candidate, profile, store, paths
):
    second = replace(candidate, port=80)
    profile = replace(profile, mode="rank", max_tests=2)
    cat = catalog(candidate)
    cat.capabilities["stealth"].append(80)
    engine = make_controller(store, paths)
    scan_id = store.new_scan([candidate, second], profile)
    await engine.scan(scan_id, cat)
    assert engine.vpn.calls == [
        ("connect", candidate.key),
        ("disconnect",),
        ("connect", second.key),
    ]
    assert store.scan(scan_id)["state"] == "complete"
    assert len(store.history()) == 2
    assert engine.vpn.current.state == State.CONNECTED


async def test_bounded_retries_count_toward_maximum(candidate, profile, store, paths):
    profile = replace(profile, retries=2, max_tests=2)
    probe = FakeProbe(Metrics(https_ok=False))
    engine = make_controller(store, paths, probe=probe)
    scan_id = store.new_scan([candidate], profile)
    await engine.scan(scan_id, catalog(candidate))
    assert len(store.history()) == 2
    assert store.scan(scan_id)["attempts"] == 2
    assert [c[0] for c in engine.vpn.calls] == ["connect", "disconnect", "connect"]


async def test_catalog_change_refuses_resume(candidate, profile, store, paths):
    scan_id = store.new_scan([candidate], profile)
    cat = catalog(candidate)
    cat.capabilities = {"stealth": [80]}
    engine = make_controller(store, paths)
    with pytest.raises(SafetyError, match="no longer available"):
        await engine.scan(scan_id, cat)
    assert not engine.vpn.calls


async def test_live_development_gate(candidate, profile, store, paths):
    vpn = FakeVPN()
    engine = make_controller(store, paths, vpn, gate=SafetyGate(True, True))
    with pytest.raises(SafetyError, match="Offline mode"):
        await engine.connect(candidate, profile)
    with pytest.raises(SafetyError):
        await engine.disconnect()
    assert not vpn.calls


def test_identity_requires_matching_protocol_and_port(candidate):
    assert owns(Status(State.CONNECTED, candidate.location.city, "stealth", 443), candidate)
    assert not owns(Status(State.CONNECTED, candidate.location.city, "tcp", 443), candidate)
    assert not owns(Status(State.CONNECTING, candidate.location.city, "stealth", 443), candidate)
    assert not owns(Status(State.CONNECTED, candidate.location.city, "stealth", 80), candidate)


async def test_stop_during_initial_status_prevents_later_connect(candidate, profile, store, paths):
    class DelayedStatus(FakeVPN):
        reading = asyncio.Event()

        async def status(self):
            self.reading.set()
            await asyncio.sleep(10)
            return await super().status()

    vpn = DelayedStatus()
    engine = make_controller(store, paths, vpn)
    scan_id = store.new_scan([candidate], profile)
    task = asyncio.create_task(engine.scan(scan_id, catalog(candidate)))
    await asyncio.wait_for(vpn.reading.wait(), 1)
    engine.stop()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not vpn.calls
    assert store.scan(scan_id)["state"] == "paused"


async def test_establishment_time_excludes_adapter_preflight(candidate, profile, store, paths):
    class PreflightVPN(FakeVPN):
        async def connect(self, candidate):
            await asyncio.sleep(0.05)
            started = time.monotonic()
            await super().connect(candidate)
            return started

    engine = make_controller(store, paths, PreflightVPN())
    result = await engine.connect(candidate, profile)
    assert result.outcome == Outcome.SUCCESS
    assert result.connect_s < 0.04
