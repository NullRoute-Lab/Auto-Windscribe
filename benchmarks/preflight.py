"""Run: uv run python -m benchmarks.preflight. Synthetic delays, never networking."""

import asyncio
import json
import platform
import socket
import statistics
import subprocess
import time
from dataclasses import replace

from auto_windscribe.demo import DemoGate
from auto_windscribe.models import Profile
from auto_windscribe.preflight import Preflight
from tests.test_preflight import Inventory, Transport, candidates


def forbidden(*args, **kwargs):
    raise AssertionError("Benchmark forbids real subprocesses and network access")


async def benchmark():
    # Independent tripwires also protect this command outside pytest.
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    socket.getaddrinfo = forbidden
    subprocess.Popen = forbidden
    asyncio.create_subprocess_exec = forbidden
    asyncio.create_subprocess_shell = forbidden
    values = candidates(32)
    measurements = {}
    for concurrency in (1, 16):
        runs, peaks = [], []
        for _ in range(3):
            transport = Transport(0.1)
            engine = Preflight(Inventory(), transport, DemoGate())
            profile = replace(
                Profile(),
                preflight_concurrency=concurrency,
                preflight_rate=100,
                preflight_retries=0,
                preflight_budget=32,
            )
            started = time.perf_counter()
            result = await engine.run(values, profile)
            runs.append(round(time.perf_counter() - started, 6))
            peaks.append(transport.peak)
            assert len(result) == transport.calls == 32
        measurements[str(concurrency)] = {
            "seconds": runs,
            "median_s": statistics.median(runs),
            "peak_concurrency": peaks,
        }
    return {
        "python": platform.python_version(),
        "platform": "Linux x86_64",
        "experiment": "32 synthetic endpoints, 100 ms delay each, three runs, 100 starts/s cap",
        "network_operations": 0,
        "results": measurements,
        "median_speedup": round(measurements["1"]["median_s"] / measurements["16"]["median_s"], 3),
        "scope": "Sequential vs bounded preflight scheduling only; no real VPN speed claim.",
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(benchmark()), indent=2))
