import asyncio
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from textual.widgets import Static

from auto_windscribe.cli import main
from auto_windscribe.demo import demo_services
from auto_windscribe.models import Outcome, State, Status, builtin_profiles
from auto_windscribe.preflight import Evidence, PreflightState
from auto_windscribe.service import build_services
from auto_windscribe.storage import Paths, Store
from auto_windscribe.tui import Confirm, WindscribeApp
from tests.fakes import FakeVPN
from tests.test_engine import catalog, make_controller


async def test_exhaustive_checkpoint_resumes_with_fresh_session_budget(
    candidate, profile, store, paths
):
    profile = replace(profile, name="exhaustive", strategy="exhaustive", mode="rank", max_tests=1)
    second = replace(candidate, port=80)
    cat = catalog(candidate)
    cat.capabilities["stealth"].append(80)
    vpn = FakeVPN()
    vpn.on_connect = lambda c: setattr(
        vpn, "current", Status(State.DISCONNECTED, logged_in=True, diagnostic="tunnel_failed")
    )
    engine = make_controller(store, paths, vpn)
    scan = store.new_scan([candidate, second], profile)
    await engine.scan(scan, cat)
    assert store.scan(scan)["state"] == "limited"
    assert len(store.pending(scan)) == 1
    await engine.scan(scan, cat)
    assert store.scan(scan)["state"] == "complete"
    assert store.scan(scan)["attempts"] == 2
    assert len(vpn.calls) == 2 and vpn.calls[0] != vpn.calls[1]


async def test_delayed_dispatch_ack_not_false_early_failure(candidate, profile, store, paths):
    class DelayedVPN(FakeVPN):
        async def connect(self, candidate):
            self.calls.append(("connect", candidate.key))
            self.since = time.monotonic()

        async def status(self):
            if self.calls and time.monotonic() - self.since > 0.07:
                self.current = Status(State.CONNECTED, candidate.location.city, "stealth", 443)
            return self.current

    profile.timeout = 0.15  # Unit phase clock; production profile validation keeps >=3s.
    result = await make_controller(store, paths, DelayedVPN())._attempt(candidate, profile)
    assert result.outcome == Outcome.SUCCESS
    assert result.observations == ["disconnected", "connected"]
    assert set(result.timings) >= {"dispatch", "establishment", "verification"}


async def test_inflight_poll_can_finish_past_establishment_deadline(
    candidate, profile, store, paths
):
    class SlowPoll(FakeVPN):
        async def status(self):
            if self.calls:
                await asyncio.sleep(0.06)
            return await super().status()

    profile.timeout = 0.02
    result = await make_controller(store, paths, SlowPoll())._attempt(candidate, profile)
    assert result.outcome == Outcome.SUCCESS


async def test_poll_timeout_is_not_native_connection_timeout(candidate, profile, store, paths):
    class SlowPoll(FakeVPN):
        async def status(self):
            if self.calls:
                await asyncio.sleep(0.2)
            return await super().status()

    profile.status_timeout = 0.02
    result = await make_controller(store, paths, SlowPoll())._attempt(candidate, profile)
    assert result.code == "status_timeout" and result.outcome == Outcome.UNKNOWN
    assert result.metrics.https_ok is None


@pytest.mark.parametrize("state", [State.DISCONNECTED, State.CONNECTING])
async def test_timeout_distinguishes_safe_next_candidate_from_uncertainty(
    candidate, profile, store, paths, state
):
    vpn = FakeVPN()
    vpn.on_connect = lambda c: setattr(vpn, "current", Status(state, logged_in=True))
    profile.timeout = 0.03
    result = await make_controller(store, paths, vpn)._attempt(candidate, profile)
    assert result.code == "connection_timeout"
    assert result.outcome == (Outcome.FAILED if state == State.DISCONNECTED else Outcome.UNKNOWN)
    assert result.observations == [state.value]
    assert len(vpn.calls) == 1


def test_preflight_persistence_does_not_create_vpn_success(candidate, profile, store):
    scan = store.new_scan([candidate], profile)
    store.record_preflight(scan, Evidence(candidate.key, PreflightState.REACHABLE, "tcp_only", 2))
    assert store.preflights(scan)[0]["tcp_ms"] == 2
    assert store.history() == [] and len(store.pending(scan)) == 1


def test_offline_and_dry_run_create_no_xdg_files(tmp_path, monkeypatch):
    paths = Paths.under(tmp_path / "untouched")
    services = build_services(paths, offline=True)
    assert services.store.scans() == []
    services.store.close()
    assert not paths.config.exists() and not paths.data.exists() and not paths.cache.exists()
    assert main(["status", "--offline"]) == 0
    assert main(["disconnect", "--dry-run"]) == 0
    assert not (tmp_path / "xdg-data").exists()


def test_readonly_history_preserves_persistent_bytes(candidate, store, paths, profile):
    store.new_scan([candidate], profile)
    before = {p.name: p.read_bytes() for p in paths.data.iterdir()}
    readonly = Store(paths.data / "history.sqlite3", readonly=True)
    assert len(readonly.scans()) == 1
    readonly.close()
    assert before == {p.name: p.read_bytes() for p in paths.data.iterdir()}


async def test_normal_tui_no_lock_and_one_enter_starts_turbo_with_fakes(paths):
    services = demo_services(paths)
    services.offline = False  # Still exclusively DemoVPN/DemoProbe/DemoGate.
    services.demo = False
    app = WindscribeApp(services)
    try:
        async with app.run_test(size=(130, 42)) as pilot:
            await app.workers.wait_for_complete()
            assert isinstance(app.screen, Confirm)
            assert services.vpn.calls == []
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert services.vpn.calls == ["connect"]
            assert "LIVE LOCKED" not in str(app.query_one("#status", Static).render())
            assert "CONNECTED" in str(app.query_one("#status", Static).render())
            await pilot.press("q")
        assert services.vpn.calls == ["connect"]
        assert not services.controller.gate.confirmed
    finally:
        services.store.close()


async def test_existing_connection_suppresses_startup_scan(paths):
    services = demo_services(paths)
    services.offline = services.demo = False
    services.vpn.current = Status(State.CONNECTED, "Other", "udp", 443)
    app = WindscribeApp(services)
    try:
        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert not isinstance(app.screen, Confirm)
            await pilot.press("q")
        assert services.vpn.calls == []
    finally:
        services.store.close()


def test_three_profiles_and_invalid_budgets(profile):
    profiles = builtin_profiles()
    assert profiles["turbo"].mode == "first-success"
    assert profiles["balanced"].mode == profiles["exhaustive"].mode == "rank"
    for field, value in [
        ("preflight_concurrency", 65),
        ("preflight_timeout", 0),
        ("preflight_budget", -1),
        ("session_budget", 0),
        ("strategy", "parallel-vpn"),
    ]:
        with pytest.raises(ValueError):
            replace(profile, **{field: value}).validate()


async def test_native_read_commands_serialized(monkeypatch):
    from auto_windscribe.process import Completed
    from auto_windscribe.safety import SafetyGate
    from auto_windscribe.windscribe import WindscribeCLI

    active = peak = 0

    async def run(argv, timeout=10):  # noqa: ASYNC109
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return Completed(0, "Login: Logged in\nConnection: Disconnected")

    cli = WindscribeCLI(AsyncMock(run=AsyncMock(side_effect=run)), SafetyGate(), "/fake/cli")
    await asyncio.gather(*(cli.status() for _ in range(10)))
    assert peak == 1


async def test_readonly_catalog_does_not_write_cache(tmp_path, monkeypatch):
    from auto_windscribe.service import Services
    from auto_windscribe.storage import Config, Profiles

    paths = Paths.under(tmp_path / "read-only")
    db = Store(paths.data / "history.sqlite3", readonly=True)
    vpn = FakeVPN()
    from tests.test_preflight import candidates

    vpn.locations = AsyncMock(return_value=[candidates(1)[0].location])
    vpn.capabilities = AsyncMock(return_value={"stealth": [443]})
    engine = make_controller(db, paths, vpn)
    services = Services(
        paths, Config(), vpn, db, Profiles(paths, readonly=True), engine, readonly=True
    )
    try:
        assert (await services.catalog()).locations
        assert not paths.cache.exists()
    finally:
        db.close()
