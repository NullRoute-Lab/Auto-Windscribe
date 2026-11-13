"""Opt-in desktop recovery. Exact verified GUI only; never signals native helpers."""

import asyncio
import configparser
import ctypes
import os
import shlex
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .errors import AppError, SafetyError
from .health import NetworkHealthChecker
from .models import Profile, State, Status
from .process import Runner
from .safety import SafetyGate
from .windscribe import VPN


def open_pidfd(pid: int) -> int:
    """glibc API: uv's Linux Python 3.12 may omit Python's optional pidfd symbols."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.pidfd_open
    function.argtypes = [ctypes.c_int, ctypes.c_uint]
    function.restype = ctypes.c_int
    fd = int(function(pid, 0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "Cannot acquire GUI process handle")
    return fd


def send_termination(fd: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    function = libc.pidfd_send_signal
    function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(fd, signal.SIGTERM, None, 0) < 0:
        raise OSError(ctypes.get_errno(), "Cannot gracefully signal GUI process handle")


@dataclass(frozen=True)
class GUIProcess:
    pid: int
    uid: int
    executable: str
    started: str


class GUIProcesses(Protocol):
    async def executable(self) -> str: ...
    async def find(self, executable: str) -> GUIProcess | None: ...
    async def terminate(self, process: GUIProcess) -> None: ...
    async def alive(self, process: GUIProcess) -> bool: ...
    async def launch(self, executable: str) -> None: ...


class LinuxGUIProcesses:
    """Resolve package desktop entry; validate UID/exe/start-time and signal via pidfd."""

    def __init__(self, runner: Runner, gate: SafetyGate) -> None:
        self.runner, self.gate = runner, gate
        self._verified: str | None = None
        self._children: list[asyncio.subprocess.Process] = []

    @staticmethod
    def trusted(path: Path) -> bool:
        return all(
            p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in (path, *path.parents)
        )

    async def executable(self) -> str:
        # Deliberately version-scoped: SIGTERM cleanup reviewed in upstream v2.25.8.
        version = await self.runner.run(["/usr/bin/rpm", "-q", "--qf", "%{VERSION}", "windscribe"])
        if version.returncode or version.stdout.strip() != "2.25.8":
            raise SafetyError("gui_unsupported", "GUI shutdown reviewed only for Windscribe 2.25.8")
        files = await self.runner.run(["/usr/bin/rpm", "-ql", "windscribe"])
        self._verified = await asyncio.to_thread(
            self.resolve_executable, files.returncode, files.stdout
        )
        return self._verified

    def resolve_executable(self, returncode: int, files: str) -> str:
        desktop = Path("/usr/share/applications/windscribe.desktop")
        if returncode or str(desktop) not in files.splitlines():
            raise SafetyError("gui_unverified", "Official package desktop entry unavailable")
        if not self.trusted(desktop):
            raise SafetyError("gui_unverified", "Desktop entry ownership is unsafe")
        entry = configparser.ConfigParser(interpolation=None)
        entry.read(desktop)
        argv = shlex.split(entry["Desktop Entry"]["Exec"])
        if not argv or any(arg not in ("%F", "%U", "%f", "%u") for arg in argv[1:]):
            raise SafetyError("gui_unverified", "Unsupported desktop launcher arguments")
        path = Path(argv[0])
        if not path.is_absolute() or str(path) not in files.splitlines():
            raise SafetyError("gui_unverified", "Launcher is not in the installed Windscribe RPM")
        resolved = path.resolve(strict=True)
        # Tie it to the official CLI's install directory, not a process-name match.
        cli = Path("/usr/bin/windscribe-cli").resolve(strict=True)
        if resolved != cli.parent / "Windscribe" or not self.trusted(resolved):
            raise SafetyError("gui_unverified", "GUI executable identity/ownership is unsafe")
        if not os.access(resolved, os.X_OK):
            raise SafetyError("gui_unverified", "GUI executable is not executable")
        return str(resolved)

    @staticmethod
    def identity(pid: int) -> GUIProcess | None:
        try:
            proc = Path("/proc") / str(pid)
            # Never read argv/environment/config: they may contain private data.
            uid = proc.stat().st_uid
            executable = str((proc / "exe").resolve(strict=True))
            stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            if stat[0] == "Z":
                return None
            return GUIProcess(pid, uid, executable, stat[19])
        except (OSError, ValueError, IndexError):
            return None

    async def find(self, executable: str) -> GUIProcess | None:
        if executable != self._verified:
            raise SafetyError("gui_unverified", "Executable was not verified")
        return await asyncio.to_thread(self.find_sync, executable)

    def find_sync(self, executable: str) -> GUIProcess | None:
        matches = []
        for path in Path("/proc").iterdir():
            if path.name.isdecimal():
                identity = self.identity(int(path.name))
                if identity and identity.executable == executable:
                    matches.append(identity)
        if len(matches) > 1 or any(p.uid != os.getuid() for p in matches):
            raise SafetyError("gui_ownership", "Multiple GUI instances or different owning user")
        return matches[0] if matches else None

    async def alive(self, process: GUIProcess) -> bool:
        return self.identity(process.pid) == process

    async def terminate(self, process: GUIProcess) -> None:
        self.gate.require_live()
        if process.uid != os.getuid() or process.executable != self._verified:
            raise SafetyError("gui_ownership", "Refusing to signal an unverified process")
        fd = open_pidfd(process.pid)
        try:
            if self.identity(process.pid) != process:
                raise SafetyError("gui_pid_changed", "GUI identity changed before shutdown")
            send_termination(fd)
        finally:
            os.close(fd)
        # No SIGKILL escalation. A stalled graceful exit pauses recovery.

    async def launch(self, executable: str) -> None:
        self.gate.require_live()
        if executable != await self.executable() or await self.find(executable) is not None:
            raise SafetyError("gui_changed", "GUI executable/instance changed before launch")
        process = await asyncio.create_subprocess_exec(
            executable,
            cwd=str(Path(executable).parent),
            start_new_session=True,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        # Launch lifetime is independent of TUI lifetime; Quit never terminates this process.
        self._children = [p for p in self._children if p.returncode is None]
        self._children.append(process)


@dataclass
class GUIReport:
    safe: bool = False
    phase: str = "GUI_UNAVAILABLE"
    reason: str = "GUI recovery not attempted"
    gui: str = "UNAVAILABLE"
    cli: str = "UNRESPONSIVE"
    native: str = "unknown"
    firewall: str = "unknown"
    internet: str = "UNKNOWN"
    restarts: int = 0
    elapsed_s: float = 0
    trigger: str = "manual"
    candidate_key: str = ""
    settlement_resolved: bool = False


class WindscribeGUIManager:
    def __init__(
        self, processes: GUIProcesses, vpn: VPN, health: NetworkHealthChecker, gate: SafetyGate
    ) -> None:
        self.processes, self.vpn, self.health, self.gate = processes, vpn, health, gate
        self.active = False
        self.suspend_cli = False
        self.used = 0
        self.blocked = False
        self.last = GUIReport()
        self.emit: Callable[[GUIReport], None] = lambda report: None
        self.poll = 0.25

    def begin_session(self) -> None:
        self.used, self.blocked = 0, False

    def phase(self, phase: str, reason: str) -> None:
        self.last.phase, self.last.reason = phase, reason
        self.emit(self.last)

    async def status(self, profile: Profile) -> Status:
        async with asyncio.timeout(profile.status_timeout):
            status = await self.vpn.status()
        self.last.native, self.last.firewall = status.state.value, status.firewall
        self.last.cli = "READY" if status.state == State.DISCONNECTED else "BUSY"
        return status

    @staticmethod
    def guard(status: Status) -> None:
        if status.state != State.DISCONNECTED:
            raise SafetyError(
                "gui_unsafe_state", "Restart requires conclusively Disconnected; tunnel retained"
            )
        if status.logged_in is not True:
            raise SafetyError("gui_login", "Authenticated account not confirmed; use official app")
        if status.firewall.casefold() != "off":
            raise SafetyError(
                "gui_firewall_policy",
                "Firewall On/unknown: GUI shutdown may alter policy; restart refused",
            )

    async def restart(
        self,
        profile: Profile,
        *,
        unresolved: bool = False,
        trigger: str = "manual",
        candidate_key: str = "",
    ) -> GUIReport:
        self.gate.require_live()
        profile.validate()
        self.last = GUIReport(trigger=trigger, candidate_key=candidate_key, restarts=self.used)
        self.active = True
        started = time.monotonic()
        try:
            if not profile.gui_restart:
                raise SafetyError("gui_opt_in", "Enable GUI restart recovery for this session")
            if unresolved:
                raise SafetyError(
                    "gui_pending_ipc", "Unresolved native request: GUI restart refused"
                )
            if self.blocked or self.used >= profile.gui_restart_limit:
                raise SafetyError(
                    "gui_limit", "GUI recovery budget exhausted or previous restart failed"
                )
            async with asyncio.timeout(
                profile.gui_stop_timeout + profile.gui_start_timeout + profile.recovery_timeout
            ):
                self.phase("GUI_RECOVERY_CHECK", "Checking exact GUI identity and native baseline")
                executable = await self.processes.executable()
                process = await self.processes.find(executable)
                self.last.gui = "RUNNING" if process else "CLOSED"
                if process is None:
                    # Status CLI can auto-launch the desktop: do not call it here.
                    raise SafetyError(
                        "gui_closed", "GUI already closed; open the official app before recovery"
                    )
                self.guard(await self.status(profile))
                await asyncio.sleep(profile.recovery_delay)
                self.guard(await self.status(profile))
                if await self.processes.find(executable) != process:
                    raise SafetyError(
                        "gui_changed", "GUI process identity changed during settlement"
                    )
                self.used += 1
                self.last.restarts = self.used
                self.suspend_cli = True
                self.phase(
                    "GUI_CLOSING", "Graceful SIGTERM to verified GUI only; no helper signals"
                )
                async with asyncio.timeout(profile.gui_stop_timeout):
                    await self.processes.terminate(process)
                    while await self.processes.alive(process):  # noqa: ASYNC110 -- injected proc polling
                        await asyncio.sleep(self.poll)
                self.last.gui, self.last.cli = "CLOSED", "UNRESPONSIVE"
                self.phase(
                    "GUI_STOPPED", "GUI exited. No CLI call while closed (it can auto-launch GUI)"
                )
                async with asyncio.timeout(profile.gui_start_timeout):
                    self.last.gui = "STARTING"
                    self.phase("GUI_STARTING", "Launching verified official desktop executable")
                    await self.processes.launch(executable)
                    new_process = None
                    while new_process is None:
                        new_process = await self.processes.find(executable)
                        await asyncio.sleep(self.poll)
                    if new_process == process:
                        raise SafetyError(
                            "gui_same_process", "Old GUI identity remains; restart not established"
                        )
                    self.last.gui = "RUNNING"
                    self.phase(
                        "CLI_REINITIALIZING", "Waiting for native CLI and authenticated account"
                    )
                    while True:
                        if not await self.processes.alive(new_process):
                            raise SafetyError("gui_start_failed", "New GUI exited before readiness")
                        try:
                            status = await self.status(profile)
                        except (AppError, TimeoutError):
                            self.last.cli = "UNRESPONSIVE"
                        else:
                            if status.state != State.UNKNOWN:
                                self.guard(status)
                                break
                        await asyncio.sleep(self.poll)
                await asyncio.sleep(profile.recovery_delay)
                self.guard(await self.status(profile))
                self.phase("NETWORK_HEALTH_CHECK", "GUI running is not proof of restored internet")
                report = await self.health.check(profile)
                self.last.internet = report.state.value
                self.guard(await self.status(profile))
                if not report.safe or report.firewall.casefold() != "off":
                    raise SafetyError(
                        "gui_baseline_failed", "GUI restarted; safe network baseline not restored"
                    )
                self.suspend_cli = False
                self.last.safe = self.last.settlement_resolved = True
                self.phase(
                    "BASELINE_VERIFIED", "Authenticated CLI ready; disconnected baseline verified"
                )
        except asyncio.CancelledError:
            self.blocked = True
            self.phase(
                "GUI_CANCELLED", "Cancelled; no further signals or launches. GUI may be closed"
            )
            raise
        except TimeoutError:
            self.blocked = True
            self.phase(
                "GUI_DEADLINE", "GUI recovery deadline exhausted; no force-kill or repeat restart"
            )
        except (AppError, OSError, ValueError, KeyError, AttributeError, configparser.Error) as exc:
            self.blocked = True
            reason = (
                str(exc)
                if isinstance(exc, AppError)
                else "GUI identity or process operation failed"
            )
            self.phase(exc.code if isinstance(exc, AppError) else "GUI_FAILED", reason)
        finally:
            self.active = False
            self.last.elapsed_s = time.monotonic() - started
            self.emit(self.last)
        return self.last
