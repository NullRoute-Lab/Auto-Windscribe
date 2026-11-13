import asyncio
import socket
import time
from dataclasses import replace

import pytest

from auto_windscribe.demo import DemoGate
from auto_windscribe.errors import SafetyError
from auto_windscribe.models import Candidate, Location, Metrics, Outcome, Result
from auto_windscribe.preflight import (
    Endpoint,
    Evidence,
    NativeInventory,
    Preflight,
    PreflightState,
    TCPTransport,
)
from auto_windscribe.ranking import adaptive_order, rank_results
from auto_windscribe.safety import SafetyGate


def candidates(n=24):
    return [
        Candidate(Location(f"Country {i % 3}", f"City {i}", f"Example {i}"), "stealth", 443)
        for i in range(n)
    ]


class Inventory:
    def __init__(self, duplicate=False):
        self.duplicate = duplicate

    async def endpoints(self, values):
        return {
            c.key: Endpoint(
                "duplicate.invalid" if self.duplicate else f"{i}.invalid",
                c.port,
                "synthetic fixture",
                time.time() + 60,
            )
            for i, c in enumerate(values)
        }


class Transport:
    def __init__(self, delay=0.025, failures=None):
        self.delay, self.failures = delay, failures or {}
        self.active = self.peak = self.calls = self.cancelled = 0
        self.starts = []

    async def tcp(self, endpoint, deadline):
        self.calls += 1
        self.starts.append(time.monotonic())
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
            if endpoint.host in self.failures:
                raise self.failures[endpoint.host]
            return 25.0
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


def configuration(profile, **kw):
    return replace(profile, preflight_concurrency=4, preflight_rate=100, preflight_retries=0, **kw)


async def test_parallel_bounded_and_deduplicated(profile):
    transport = Transport(0.08)
    preflight = Preflight(Inventory(), transport, DemoGate())
    results = await preflight.run(candidates(), configuration(profile))
    assert 2 <= transport.peak <= 4
    assert transport.active == 0
    assert len(results) == 24
    assert all(r.state == PreflightState.REACHABLE for r in results.values())
    duplicate = Transport()
    await Preflight(Inventory(True), duplicate, DemoGate()).run(candidates(), profile)
    assert duplicate.calls == 1


async def test_total_budget_includes_retries_and_rate_limits(profile):
    transport = Transport(0.001, {"0.invalid": OSError(), "1.invalid": OSError()})
    preflight = Preflight(Inventory(), transport, DemoGate())
    results = await preflight.run(
        candidates(), replace(profile, preflight_budget=5, preflight_rate=50)
    )
    assert preflight.used == transport.calls == 5
    assert all(b - a >= 0.018 for a, b in zip(transport.starts, transport.starts[1:], strict=False))
    assert any(r.code == "budget_exhausted" for r in results.values())


async def test_timeout_is_per_probe_and_partial_failures_survive(profile):
    class Mixed(Transport):
        async def tcp(self, endpoint, deadline):
            if endpoint.host == "0.invalid":
                await asyncio.sleep(5)
            return await super().tcp(endpoint, deadline)

    transport = Mixed(
        0.001, {"1.invalid": socket.gaierror(), "2.invalid": ConnectionRefusedError()}
    )
    started = time.monotonic()
    results = list(
        (
            await Preflight(Inventory(), transport, DemoGate()).run(
                candidates(4), configuration(profile, preflight_timeout=0.1)
            )
        ).values()
    )
    assert 0.09 <= time.monotonic() - started < 0.5
    assert [r.code for r in results] == ["tcp_timeout", "dns_failed", "tcp_refused", "tcp_only"]


async def test_cancellation_cleans_all_tasks_and_keeps_partial_evidence(profile):
    transport = Transport(10)
    observed = []
    preflight = Preflight(Inventory(), transport, DemoGate())
    task = asyncio.create_task(
        preflight.run(candidates(), configuration(profile), lambda e, n, t: observed.append(e))
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    calls = transport.calls
    await asyncio.sleep(0.03)
    assert transport.active == 0 and calls == transport.calls
    assert transport.cancelled > 0
    assert observed and all(e.state == PreflightState.CANCELLED for e in observed)


async def test_native_missing_inventory_and_udp_never_probed(profile):
    transport = Transport()
    values = candidates(1)
    results = await Preflight(NativeInventory(), transport, DemoGate()).run(values, profile)
    assert results[values[0].key].code == "endpoint_unavailable"
    udp = [replace(values[0], protocol=p) for p in ("udp", "wireguard")]
    results = await Preflight(Inventory(), transport, DemoGate()).run(udp, profile)
    assert all(r.code == "udp_uncertain" and r.tcp_ms is None for r in results.values())
    assert transport.calls == 0


async def test_gate_prevents_inventory_and_tcp(profile):
    class ForbiddenInventory:
        async def endpoints(self, values):
            pytest.fail("Inventory must not be consulted")

    with pytest.raises(SafetyError):
        await Preflight(ForbiddenInventory(), Transport(), SafetyGate(confirmed=True)).run(
            candidates(), profile
        )
    with pytest.raises(SafetyError):
        await TCPTransport(SafetyGate(confirmed=True)).tcp(
            Endpoint("example.invalid", 443, "fixture", time.time() + 60), time.monotonic() + 1
        )


async def test_tcp_dns_cache_single_flight_and_private_targets_rejected(monkeypatch):
    # Only DNS is faked; no socket is ever connected in this test.
    loop = asyncio.get_running_loop()
    calls = []

    async def dns(*args, **kwargs):
        calls.append(args)
        await asyncio.sleep(0.01)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", dns)
    transport = TCPTransport(DemoGate())
    await asyncio.gather(*(transport._resolve("trusted.invalid") for _ in range(8)))
    assert len(calls) == 1
    with pytest.raises(ValueError):
        await transport._resolve("127.0.0.1")


async def test_stale_inventory_not_probed(profile):
    class Stale(Inventory):
        async def endpoints(self, values):
            return {c.key: Endpoint("example.invalid", 443, "official", 0) for c in values}

    transport = Transport()
    results = await Preflight(Stale(), transport, DemoGate()).run(candidates(), profile)
    assert all(r.code == "metadata_untrusted" for r in results.values())
    assert transport.calls == 0


def test_tcp_evidence_is_never_a_verified_result(profile):
    values = candidates(4)
    evidence = {values[1].key: Evidence(values[1].key, PreflightState.REACHABLE, "tcp_only", 1)}
    ordered = adaptive_order(values, profile, [], evidence)
    assert ordered[0] == values[1]
    assert rank_results([]) == []
    failed = Result(values[1], Outcome.FAILED, "https_failed", "Synthetic", metrics=Metrics())
    assert rank_results([failed])[0].verified is None


def test_history_and_diversity_never_permanently_exclude(profile):
    values = candidates(9)
    values = [
        replace(c, protocol=("stealth", "udp", "wireguard")[i % 3]) for i, c in enumerate(values)
    ]
    history = [
        Result(
            values[0],
            Outcome.SUCCESS,
            "verified",
            "Synthetic",
            metrics=Metrics(20, 0, 1, True),
            context=profile.context,
        )
    ]
    ordered = adaptive_order(values, profile, history)
    assert ordered[0] == values[0]
    assert len({c.protocol for c in ordered[:3]}) == 3
    assert set(c.key for c in ordered) == set(c.key for c in values)
    history.append(
        Result(values[1], Outcome.FAILED, "failed", "Synthetic", context=profile.context)
    )
    assert values[1] in adaptive_order(values, profile, history)


async def test_real_tcp_backend_closes_its_socket_on_cancellation(monkeypatch):
    class Socket:
        closed = False

        def setblocking(self, value):
            assert value is False

        def close(self):
            self.closed = True

    sock = Socket()
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: sock)
    started = asyncio.Event()

    async def connect(fake, address):
        assert fake is sock and address == ("1.1.1.1", 443)
        started.set()
        await asyncio.sleep(10)

    monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", connect)
    transport = TCPTransport(DemoGate())
    task = asyncio.create_task(
        transport.tcp(Endpoint("1.1.1.1", 443, "synthetic", time.time() + 60), time.monotonic() + 1)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert sock.closed


async def test_batch_deadline_cleans_workers_and_keeps_successes(profile):
    class Mixed(Transport):
        async def tcp(self, endpoint, deadline):
            if endpoint.host == "0.invalid":
                return 1.0
            return await super().tcp(endpoint, deadline)

    transport = Mixed(10)
    engine = Preflight(Inventory(), transport, DemoGate())
    started = time.monotonic()
    results = await engine.run(
        candidates(4), replace(profile, preflight_deadline=1, preflight_timeout=5)
    )
    assert 0.9 <= time.monotonic() - started < 1.5
    assert transport.active == 0
    assert list(results.values())[0].state == PreflightState.REACHABLE
    assert any(r.state == PreflightState.CANCELLED for r in results.values())
