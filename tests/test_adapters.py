import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from auto_windscribe.errors import AppError, SafetyError
from auto_windscribe.probes import TunnelProbe
from auto_windscribe.process import AsyncRunner, Completed
from auto_windscribe.safety import OperationLock, SafetyGate
from auto_windscribe.signals import cancellation_signals
from auto_windscribe.windscribe import WindscribeCLI
from tests.fakes import ScriptRunner

CLI = "/usr/bin/windscribe-cli"


def enabled_gate(monkeypatch):
    # Only fake Runner instances are passed to native adapters in these tests.
    monkeypatch.delenv("AUTO_WINDSCRIBE_OFFLINE")
    return SafetyGate(True, True)


async def test_cli_nickname_argv_and_capability_check(monkeypatch, candidate):
    runner = ScriptRunner(
        [
            ([CLI, "help", "connect"], "  protocol     stealth"),
            ([CLI, "ports", "stealth"], "443, 80"),
            ([CLI, "locations"], "Test Region - Test City - Example"),
            ([CLI, "status"], "Login: Logged in\nConnection: Disconnected"),
            ([CLI, "connect", "-n", "Example", "stealth:443"], "Request sent"),
        ]
    )
    cli = WindscribeCLI(runner, enabled_gate(monkeypatch), CLI)
    await cli.connect(candidate)
    assert not runner.responses


async def test_cli_rejects_unsupported_port(monkeypatch, candidate):
    runner = ScriptRunner(
        [([CLI, "help", "connect"], "  protocol     stealth"), ([CLI, "ports", "stealth"], "80")]
    )
    with pytest.raises(SafetyError, match="no longer supported"):
        await WindscribeCLI(runner, enabled_gate(monkeypatch), CLI).connect(candidate)
    assert all("connect" not in args[1:2] for args in runner.calls)


async def test_cli_never_leaks_stderr():
    runner = ScriptRunner([([CLI, "status"], Completed(1, "", "token=do-not-print"))])
    with pytest.raises(AppError) as exc:
        await WindscribeCLI(runner, SafetyGate(), CLI).status()
    assert "do-not-print" not in str(exc.value)


async def test_locked_adapter_never_dispatches(candidate):
    runner = ScriptRunner([])
    cli = WindscribeCLI(runner, SafetyGate(True, True), CLI)
    with pytest.raises(SafetyError):
        await cli.connect(candidate)
    with pytest.raises(SafetyError):
        await cli.disconnect()
    assert not runner.calls


@pytest.mark.parametrize("enabled,confirmed", [(False, False), (True, False), (False, True)])
def test_both_permissions_required(monkeypatch, enabled, confirmed):
    monkeypatch.delenv("AUTO_WINDSCRIBE_OFFLINE")
    with pytest.raises(SafetyError):
        SafetyGate(enabled, confirmed).require_live()


def test_two_process_lock_objects_conflict(paths):
    one, two = (
        OperationLock(paths.data / "operation.lock"),
        OperationLock(paths.data / "operation.lock"),
    )
    with one.hold():
        with pytest.raises(SafetyError):
            with two.hold():
                pytest.fail("lock was not exclusive")
    with two.hold():
        assert two.busy


class Process:
    def __init__(self):
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.returncode = None
        self.terminated = self.killed = False
        self.ended = asyncio.Event()

    async def wait(self):
        await self.ended.wait()
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self.ended.set()
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    def kill(self):
        self.killed = True
        self.terminate()


async def test_process_timeout_cleanup_is_only_spawned_child(monkeypatch):
    process = Process()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(AppError, match="timed out"):
        await AsyncRunner().run([CLI, "status"], timeout=0.01)
    assert process.terminated
    assert spawn.call_args.args == (CLI, "status")
    assert "shell" not in spawn.call_args.kwargs


async def test_process_cancellation_cleanup(monkeypatch):
    process = Process()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(AsyncRunner().run([CLI, "status"]))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated


async def test_process_output_limit(monkeypatch):
    process = Process()
    process.stdout.feed_data(b"x" * 1_100_000)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(AppError, match="output exceeded"):
        await AsyncRunner().run([CLI, "locations"])
    assert process.terminated


async def test_probe_guard_precedes_even_route_reads(profile):
    runner = ScriptRunner([])
    with pytest.raises(SafetyError):
        await TunnelProbe(runner, SafetyGate(True, True)).measure(profile)
    assert not runner.calls


async def test_non_tunnel_route_sends_no_probe(monkeypatch, profile):
    runner = ScriptRunner(
        [
            (["/usr/sbin/ip", "-j", "route", "get", "1.1.1.1"], '[{"dev":"eth0"}]'),
            (
                ["/usr/sbin/ip", "-j", "-d", "link", "show", "dev", "eth0"],
                '[{"linkinfo":{"info_kind":"ether"}}]',
            ),
        ]
    )
    with pytest.raises(AppError, match="not a recognizable"):
        await TunnelProbe(runner, enabled_gate(monkeypatch)).measure(profile)
    assert len(runner.calls) == 2


async def test_probes_bound_to_verified_interface(monkeypatch, profile):
    class Runner:
        calls = []

        async def run(self, argv, timeout=10):  # noqa: ASYNC109
            self.calls.append(argv)
            if argv[0].endswith("ip"):
                return Completed(
                    0,
                    json.dumps(
                        [{"dev": "tun0"}]
                        if "route" in argv
                        else [{"linkinfo": {"info_kind": "tun"}}]
                    ),
                )
            if argv[0].endswith("curl"):
                assert "if!tun0" in argv and "--noproxy" in argv and "-q" in argv
                return Completed(0, "200")
            assert argv[0].endswith("ping") and argv[argv.index("-I") + 1] == "tun0"
            return Completed(1, "4 packets transmitted, 0 received, 100% packet loss")

    runner = Runner()
    metrics = await TunnelProbe(runner, enabled_gate(monkeypatch)).measure(profile)
    assert metrics.https_ok and metrics.loss_pct == 100 and metrics.rtt_ms is None
    assert len(runner.calls) == 4


async def test_signals_cancel_only_and_restore(monkeypatch):
    loop = asyncio.get_running_loop()
    callbacks = {}
    removed = []
    called = []
    monkeypatch.setattr(loop, "add_signal_handler", lambda sig, fn: callbacks.update({sig: fn}))
    monkeypatch.setattr(loop, "remove_signal_handler", lambda sig: removed.append(sig))
    with cancellation_signals(lambda: called.append("cancel")):
        for fn in callbacks.values():
            fn()
    assert called == ["cancel", "cancel"]
    assert len(removed) == 2


async def test_optional_download_is_bounded_and_off_by_default(monkeypatch, profile):
    class Runner:
        downloads = 0

        async def run(self, argv, timeout=10):  # noqa: ASYNC109
            if argv[0].endswith("ip"):
                return Completed(
                    0,
                    json.dumps(
                        [{"dev": "tun0"}]
                        if "route" in argv
                        else [{"linkinfo": {"info_kind": "tun"}}]
                    ),
                )
            if "https://speed.cloudflare.com/__down?bytes=1000000" in argv:
                self.downloads += 1
                assert argv[argv.index("--max-filesize") + 1] == "1000000"
                assert "if!tun0" in argv
                return Completed(0, "200 1000000 250000")
            return Completed(0, "200")

    from dataclasses import replace

    runner = Runner()
    probe = TunnelProbe(runner, enabled_gate(monkeypatch))
    normal = await probe.measure(replace(profile, ping_count=0))
    assert runner.downloads == 0 and normal.download_mbps is None
    optional = await probe.measure(replace(profile, ping_count=0, download=True))
    assert optional.download_mbps == 2
    assert runner.downloads == 1


async def test_ambiguous_nickname_never_dispatches(monkeypatch, candidate):
    runner = ScriptRunner(
        [
            ([CLI, "help", "connect"], "  protocol     stealth"),
            ([CLI, "ports", "stealth"], "443"),
            (
                [CLI, "locations"],
                "Test Region - Test City - Example\nOther Region - Other City - Example",
            ),
        ]
    )
    with pytest.raises(SafetyError, match="unavailable or ambiguous"):
        await WindscribeCLI(runner, enabled_gate(monkeypatch), CLI).connect(candidate)
    assert all(args[1] != "connect" for args in runner.calls)
