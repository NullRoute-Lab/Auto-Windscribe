"""Only injected fake processes/native/network adapters. Never signal the desktop."""

import asyncio
import os
import signal
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from auto_windscribe import gui_manager
from auto_windscribe.demo import DemoGate
from auto_windscribe.errors import AppError
from auto_windscribe.gui_manager import GUIProcess, LinuxGUIProcesses, WindscribeGUIManager
from auto_windscribe.gui_manager import open_pidfd as libc_open_pidfd
from auto_windscribe.gui_manager import send_termination as libc_send_termination
from auto_windscribe.health import classify
from auto_windscribe.models import Outcome, Result, State, Status
from auto_windscribe.process import Completed
from auto_windscribe.recovery import NetworkRecoveryManager, RecoveryReport
from auto_windscribe.safety import SafetyGate
from auto_windscribe.storage import Store
from tests.fakes import FakeVPN
from tests.test_engine import catalog, make_controller
from tests.test_settlement import fast


def disconnected(firewall="off"):
    return Status(State.DISCONNECTED, firewall=firewall, logged_in=True)


def baseline(ok=True, firewall="off"):
    return classify(disconnected(firewall), ok, ok, ok, ok, "present")


class Processes:
    def __init__(self):
        self.original = GUIProcess(101, 1000, "/verified/Windscribe", "123")
        self.current = self.original
        self.calls = []
        self.stuck = False
        self.fail_launch = False
        self.stopped = asyncio.Event()

    async def executable(self):
        return self.original.executable

    async def find(self, executable):
        assert executable == self.original.executable
        return self.current

    async def alive(self, process):
        return self.current == process

    async def terminate(self, process):
        assert process == self.original
        self.calls.append(("terminate", process.pid))
        if not self.stuck:
            self.current = None
            self.stopped.set()

    async def launch(self, executable):
        assert self.current is None
        self.calls.append(("launch", executable))
        if self.fail_launch:
            raise OSError("synthetic")
        self.current = GUIProcess(102, 1000, executable, "456")


def setup(profile, ok=True):
    vpn, processes = FakeVPN(), Processes()
    vpn.current = disconnected()
    checker = AsyncMock(check=AsyncMock(return_value=baseline(ok)))
    manager = WindscribeGUIManager(processes, vpn, checker, DemoGate())
    manager.poll = 0.001
    return manager, vpn, processes, replace(fast(profile), gui_restart=True)


async def test_graceful_restart_readiness_and_no_status_while_closed(profile):
    m, vpn, processes, p = setup(profile)
    states = [
        disconnected(),
        disconnected(),
        AppError("ipc", "starting"),
        Status(),
        disconnected(),
        disconnected(),
        disconnected(),
    ]

    async def status():
        assert processes.current is not None, "CLI must not auto-launch a closed GUI"
        result = states.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    vpn.status = status
    phases = []
    m.emit = lambda r: phases.append(r.phase)
    r = await m.restart(p, trigger="native_manual_failure", candidate_key="example")
    assert r.safe and r.settlement_resolved and r.restarts == 1
    assert r.trigger == "native_manual_failure" and r.candidate_key == "example"
    assert r.cli == "READY" and r.gui == "RUNNING"
    assert processes.calls == [("terminate", 101), ("launch", "/verified/Windscribe")]
    assert phases[:5] == [
        "GUI_RECOVERY_CHECK",
        "GUI_CLOSING",
        "GUI_STOPPED",
        "GUI_STARTING",
        "CLI_REINITIALIZING",
    ]
    assert phases[-1] == "BASELINE_VERIFIED"
    assert not vpn.calls


@pytest.mark.parametrize(
    "state", [State.CONNECTED, State.CONNECTING, State.DISCONNECTING, State.UNKNOWN]
)
async def test_no_restart_of_working_external_or_unsettled_tunnel(profile, state):
    m, vpn, processes, p = setup(profile)
    vpn.current = replace(disconnected(), state=state)
    r = await m.restart(p)
    assert r.phase == "gui_unsafe_state" and not r.safe
    assert processes.calls == [] and vpn.calls == []


@pytest.mark.parametrize("firewall", ["on", "always on", "unknown"])
async def test_firewall_policy_refuses_before_signal(profile, firewall):
    m, vpn, processes, p = setup(profile)
    vpn.current = disconnected(firewall)
    r = await m.restart(p)
    assert r.phase == "gui_firewall_policy" and r.firewall == firewall
    assert processes.calls == []


async def test_gui_already_closed_does_not_call_cli(profile):
    m, vpn, processes, p = setup(profile)
    processes.current = None
    vpn.status = AsyncMock()
    r = await m.restart(p)
    assert r.phase == "gui_closed"
    vpn.status.assert_not_awaited()
    assert processes.calls == []


async def test_opt_in_and_unresolved_request(profile):
    m, vpn, processes, p = setup(profile)
    assert (await m.restart(profile)).phase == "gui_opt_in"
    m.begin_session()
    assert (await m.restart(p, unresolved=True)).phase == "gui_pending_ipc"
    assert processes.calls == []


async def test_status_unavailable_before_signal(profile):
    m, vpn, processes, p = setup(profile)
    vpn.status = AsyncMock(side_effect=AppError("ipc_timeout", "Unavailable"))
    assert not (await m.restart(p)).safe
    assert processes.calls == []


async def test_launch_failure_blocks_further_restart(profile):
    m, vpn, processes, p = setup(profile)
    processes.fail_launch = True
    r = await m.restart(p)
    assert not r.safe and r.phase == "GUI_FAILED"
    before = processes.calls.copy()
    assert (await m.restart(p)).phase == "gui_limit"
    assert processes.calls == before


async def test_graceful_timeout_no_force_kill(profile):
    m, vpn, processes, p = setup(profile)
    processes.stuck = True
    r = await m.restart(replace(p, gui_stop_timeout=1))
    assert not r.safe and r.phase == "GUI_DEADLINE"
    assert processes.calls == [("terminate", 101)]


async def test_startup_timeout_no_second_launch(profile):
    m, vpn, processes, p = setup(profile)

    async def status():
        if processes.current == processes.original:
            return disconnected()
        raise AppError("ipc_timeout", "still starting")

    vpn.status = status
    r = await m.restart(replace(p, gui_start_timeout=1))
    assert r.phase == "GUI_DEADLINE" and len(processes.calls) == 2


@pytest.mark.parametrize(
    "state,firewall,login",
    [
        (State.CONNECTED, "off", True),
        (State.CONNECTING, "off", True),
        (State.DISCONNECTED, "on", True),
        (State.DISCONNECTED, "off", False),
    ],
)
async def test_relaunch_external_autoconnect_firewall_login_conflicts(
    profile, state, firewall, login
):
    m, vpn, processes, p = setup(profile)

    async def status():
        if processes.current == processes.original:
            return disconnected()
        return Status(state, firewall=firewall, logged_in=login)

    vpn.status = status
    r = await m.restart(p)
    assert not r.safe and len(processes.calls) == 2
    assert vpn.calls == []  # never disconnect an external/auto-connected tunnel


async def test_restarted_gui_is_not_internet_recovery(profile):
    m, vpn, processes, p = setup(profile, False)
    r = await m.restart(p)
    assert r.phase == "gui_baseline_failed" and not r.safe
    assert r.gui == "RUNNING" and r.internet == "NO_CONNECTIVITY"
    assert len(processes.calls) == 2


async def test_cancel_while_closed_never_launches_or_signals_again(profile):
    m, vpn, processes, p = setup(profile)
    waiting = asyncio.Event()

    async def launch(executable):
        waiting.set()
        await asyncio.sleep(10)

    processes.launch = launch
    task = asyncio.create_task(m.restart(p))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert m.last.phase == "GUI_CANCELLED" and not m.active
    assert processes.calls == [("terminate", 101)]


async def test_session_restart_limit(profile):
    m, vpn, processes, p = setup(profile)
    p = replace(p, gui_restart_limit=1)
    assert (await m.restart(p)).safe
    assert (await m.restart(p)).phase == "gui_limit"
    assert len(processes.calls) == 2


async def test_exact_pidfd_signal_and_pid_reuse_refusal(monkeypatch):
    backend = LinuxGUIProcesses(AsyncMock(), DemoGate())
    backend._verified = "/verified/Windscribe"
    identity = GUIProcess(123, os.getuid(), backend._verified, "456")
    calls = []
    monkeypatch.setattr(gui_manager, "open_pidfd", lambda pid: calls.append(("open", pid)) or 9)
    monkeypatch.setattr(os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(
        gui_manager, "send_termination", lambda fd: calls.append(("signal", fd, signal.SIGTERM))
    )
    monkeypatch.setattr(backend, "identity", lambda pid: identity)
    await backend.terminate(identity)
    assert calls == [("open", 123), ("signal", 9, signal.SIGTERM), ("close", 9)]
    calls.clear()
    monkeypatch.setattr(backend, "identity", lambda pid: replace(identity, started="changed"))
    with pytest.raises(AppError, match="identity changed"):
        await backend.terminate(identity)
    assert calls == [("open", 123), ("close", 9)]


@pytest.mark.parametrize(
    "change",
    [
        {"uid": -1},
        {"executable": "/verified/helper"},
        {"executable": "/verified/windscribeopenvpn"},
    ],
)
async def test_no_helper_or_other_user_signal(change):
    backend = LinuxGUIProcesses(AsyncMock(), DemoGate())
    backend._verified = "/verified/Windscribe"
    identity = replace(GUIProcess(123, os.getuid(), backend._verified, "456"), **change)
    with pytest.raises(AppError):
        await backend.terminate(identity)


async def test_offline_guard_precedes_process_access(profile):
    processes = AsyncMock()
    manager = WindscribeGUIManager(processes, FakeVPN(), AsyncMock(), SafetyGate(confirmed=True))
    with pytest.raises(AppError, match="Offline"):
        await manager.restart(replace(profile, gui_restart=True))
    processes.executable.assert_not_awaited()


async def test_package_version_is_fail_closed():
    runner = AsyncMock(run=AsyncMock(return_value=Completed(0, "2.26.1")))
    with pytest.raises(AppError, match="2.25.8"):
        await LinuxGUIProcesses(runner, DemoGate()).executable()
    runner.run.assert_awaited_once_with(["/usr/bin/rpm", "-q", "--qf", "%{VERSION}", "windscribe"])


async def test_native_failure_recovers_gui_then_next_candidate(candidate, profile, store, paths):
    m, vpn, processes, p = setup(profile)
    p = replace(p, max_tests=2)
    engine = make_controller(store, paths, vpn)
    engine.gui = m
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)
    engine.recovery.recover = AsyncMock(
        side_effect=[
            RecoveryReport(phase="RECOVERY_EXHAUSTED"),
            RecoveryReport(safe=True, settlement_resolved=True),
        ]
    )

    async def attempt(c, p):
        vpn.calls.append(("connect", c.key))
        return Result(c, Outcome.FAILED, "tunnel_failed", "Native failure", phase="establishment")

    engine._attempt = attempt
    second = replace(candidate, port=80)
    cat = catalog(candidate)
    cat.capabilities[candidate.protocol].append(80)
    scan = store.new_scan([candidate, second], p)
    await engine.scan(scan, cat)
    assert vpn.calls == [("connect", candidate.key), ("connect", second.key)]
    assert len(processes.calls) == 2
    assert store.manifest(scan)["remaining"] == 0
    assert len(store.history()) == 2
    assert store.conn.execute("SELECT count(*) FROM attempt_recovery").fetchone()[0] == 3
    assert store.manifest(scan)["sessions"][0]["attempts"] == 2


async def test_timeout_alone_never_claims_popup_or_restarts(candidate, profile, store, paths):
    m, vpn, processes, p = setup(profile)
    engine = make_controller(store, paths, vpn)
    engine.gui = m
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)
    engine.recovery.recover = AsyncMock(return_value=RecoveryReport())
    engine._attempt = AsyncMock(
        return_value=Result(
            candidate,
            Outcome.UNKNOWN,
            "connection_timeout",
            "Unknown cause",
            observations=["connecting"],
        )
    )
    scan = store.new_scan([candidate], p)
    await engine.scan(scan, catalog(candidate))
    assert store.scan(scan)["state"] == "paused" and processes.calls == []


async def test_unknown_deferred_restart_then_explicit_retry(candidate, profile, paths):
    path = paths.data / "durable.sqlite3"
    store = Store(path)
    p = fast(profile)
    scan = store.new_scan([candidate], p)
    store.record(
        Result(
            candidate,
            Outcome.UNKNOWN,
            "connection_timeout",
            "Unknown",
            phase="establishment",
            observations=["connecting"],
        ),
        scan,
        0,
        complete=False,
    )
    store.close()  # process/system restart: no regeneration or network assumptions
    store = Store(path)
    vpn = FakeVPN()
    engine = make_controller(store, paths, vpn)
    engine.recovery = NetworkRecoveryManager(
        vpn,
        AsyncMock(
            check=AsyncMock(
                return_value=classify(Status(State.DISCONNECTED), True, True, True, True, "present")
            )
        ),
        engine._disconnect,
    )
    await engine.scan(scan, catalog(candidate))
    assert not vpn.calls
    manifest = store.manifest(scan)
    assert manifest["attempted"] == manifest["deferred"] == manifest["unknown"] == 1
    assert manifest["remaining"] == 0
    assert store.retry_deferred(scan) == 1
    assert store.previous(scan, candidate) is None
    assert store.history()[0].outcome == Outcome.UNKNOWN
    await engine.scan(scan, catalog(candidate))
    assert vpn.calls == [("connect", candidate.key)]
    assert store.manifest(scan)["deferred"] == 0
    store.close()


async def test_controller_excludes_other_instances_during_gui_recovery(profile, paths, store):
    m, vpn, processes, p = setup(profile)
    first = make_controller(store, paths, vpn)
    second = make_controller(store, paths, vpn)
    first.gui = m
    second.gui = m
    entered = asyncio.Event()
    release = asyncio.Event()

    async def terminate(process):
        entered.set()
        await release.wait()
        processes.current = None

    processes.terminate = terminate
    task = asyncio.create_task(first.recover_gui(p))
    await entered.wait()
    with pytest.raises(AppError, match="Another Auto-Windscribe"):
        await second.recover_gui(p)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert m.suspend_cli and m.last.phase == "GUI_CANCELLED"


async def test_session_cancellation_during_restart_preserves_attempt_and_queue(
    candidate, profile, paths, store
):
    m, vpn, processes, p = setup(profile)
    engine = make_controller(store, paths, vpn)
    engine.gui = m
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)
    engine.recovery.recover = AsyncMock(return_value=RecoveryReport())
    engine._attempt = AsyncMock(
        return_value=Result(
            candidate, Outcome.FAILED, "tunnel_failed", "Native failure", phase="establishment"
        )
    )
    entered = asyncio.Event()

    async def terminate(process):
        entered.set()
        await asyncio.sleep(10)

    processes.terminate = terminate
    other = replace(candidate, port=80)
    scan = store.new_scan([candidate, other], p)
    cat = catalog(candidate)
    cat.capabilities[candidate.protocol].append(80)
    task = asyncio.create_task(engine.scan(scan, cat))
    await entered.wait()
    engine.stop()  # same cancellation path as the unchanged outer session deadline
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.scan(scan)["state"] == "paused"
    assert len(store.history()) == 1 and store.history()[0].code == "tunnel_failed"
    assert len(store.pending(scan)) == 2
    assert store.recovery_status()["phase"] == "GUI_CANCELLED"
    assert store.manifest(scan)["sessions"][0]["attempts"] == 1
    assert processes.calls == []


async def test_gui_opt_in_can_apply_to_existing_job_without_rebuilding(
    candidate, profile, paths, store
):
    m, vpn, processes, p = setup(profile)
    engine = make_controller(store, paths, vpn)
    engine.gui = m
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)
    engine.recovery.recover = AsyncMock(return_value=RecoveryReport())
    engine._attempt = AsyncMock(
        return_value=Result(
            candidate, Outcome.FAILED, "tunnel_failed", "Native failure", phase="establishment"
        )
    )
    scan = store.new_scan([candidate], replace(p, gui_restart=False))
    await engine.scan(scan, catalog(candidate), gui_restart=True)
    assert len(processes.calls) == 2
    assert store.manifest(scan)["profile"]["gui_restart"] is False
    assert store.manifest(scan)["total"] == 1 and store.manifest(scan)["remaining"] == 0
    assert len(store.scans()) == 1


def test_untrusted_desktop_entry_and_unlisted_executable_fail(monkeypatch):
    backend = LinuxGUIProcesses(AsyncMock(), DemoGate())
    with pytest.raises(AppError, match="entry unavailable"):
        backend.resolve_executable(0, "/unrelated/app.desktop")
    monkeypatch.setattr(backend, "trusted", lambda path: False)
    with pytest.raises(AppError, match="ownership is unsafe"):
        backend.resolve_executable(0, "/usr/share/applications/windscribe.desktop")


def test_verified_desktop_entry_resolves_only_packaged_gui(monkeypatch):
    import configparser
    from pathlib import Path

    backend = LinuxGUIProcesses(AsyncMock(), DemoGate())

    def read(entry, path):
        entry.read_dict({"Desktop Entry": {"Exec": "/opt/windscribe/Windscribe %F"}})

    monkeypatch.setattr(configparser.ConfigParser, "read", read)
    monkeypatch.setattr(backend, "trusted", lambda path: True)
    monkeypatch.setattr(
        Path,
        "resolve",
        lambda path, strict=True: (
            Path("/opt/windscribe/windscribe-cli")
            if str(path) == "/usr/bin/windscribe-cli"
            else path
        ),
    )
    monkeypatch.setattr(os, "access", lambda *args: True)
    listed = "/usr/share/applications/windscribe.desktop\n/opt/windscribe/Windscribe"
    assert backend.resolve_executable(0, listed) == "/opt/windscribe/Windscribe"
    with pytest.raises(AppError, match="not in the installed"):
        backend.resolve_executable(0, listed.replace("/opt/windscribe/Windscribe", "/other"))


@pytest.mark.parametrize(
    "bad",
    [
        {"gui_restart": "yes"},
        {"gui_restart_limit": 0},
        {"gui_restart_limit": 1.5},
        {"gui_stop_timeout": 0},
        {"gui_start_timeout": 999},
    ],
)
def test_gui_profile_bounds(profile, bad):
    with pytest.raises(ValueError):
        replace(profile, **bad).validate()


async def test_unknown_with_later_native_failure_is_deferred_after_gui_recovery(
    candidate, profile, paths, store
):
    m, vpn, processes, p = setup(profile)
    engine = make_controller(store, paths, vpn)
    engine.gui = m
    engine.recovery = NetworkRecoveryManager(vpn, AsyncMock(), engine._disconnect)
    engine.recovery.recover = AsyncMock(
        return_value=RecoveryReport(
            phase="RECOVERY_EXHAUSTED", timeline=[{"diagnostic": "tunnel_failed"}]
        )
    )
    engine._attempt = AsyncMock(
        return_value=Result(
            candidate,
            Outcome.UNKNOWN,
            "connection_timeout",
            "Unknown",
            phase="establishment",
            observations=["connecting"],
        )
    )
    scan = store.new_scan([candidate], p)
    await engine.scan(scan, catalog(candidate))
    assert len(processes.calls) == 2
    assert store.history()[0].outcome == Outcome.UNKNOWN
    manifest = store.manifest(scan)
    assert manifest["deferred"] == manifest["unknown"] == 1 and manifest["remaining"] == 0
    assert store.recovery_status()["settlement_resolved"]


async def test_other_job_crash_reservation_blocks_new_native_operation(
    candidate, profile, paths, store
):
    first = store.new_scan([candidate], profile)
    store.reserve(first, candidate)
    second = store.new_scan([candidate], profile)
    engine = make_controller(store, paths)
    with pytest.raises(AppError, match="unresolved"):
        await engine.scan(second, catalog(candidate))
    with pytest.raises(AppError, match="unresolved"):
        await engine.connect(candidate, profile)
    assert not engine.vpn.calls


@pytest.mark.parametrize("fails", [False, True])
def test_libc_pidfd_binding_exact_arguments_without_syscalls(monkeypatch, fails):
    import ctypes
    from types import SimpleNamespace
    from unittest.mock import Mock

    # Capture the original functions from their globals via aliases imported at collection time.
    library = SimpleNamespace(
        pidfd_open=Mock(return_value=-1 if fails else 9),
        pidfd_send_signal=Mock(return_value=-1 if fails else 0),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: library)
    monkeypatch.setattr(ctypes, "get_errno", lambda: 1)
    if fails:
        with pytest.raises(OSError):
            libc_open_pidfd(123)
        with pytest.raises(OSError):
            libc_send_termination(9)
    else:
        assert libc_open_pidfd(123) == 9
        libc_send_termination(9)
    library.pidfd_open.assert_called_once_with(123, 0)
    library.pidfd_send_signal.assert_called_once_with(9, signal.SIGTERM, None, 0)
