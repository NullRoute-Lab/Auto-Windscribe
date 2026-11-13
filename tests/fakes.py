import asyncio
from collections import deque
from dataclasses import replace

from auto_windscribe.models import Metrics, State, Status
from auto_windscribe.process import Completed


class ScriptRunner:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    async def run(self, argv, timeout=10):  # noqa: ASYNC109
        self.calls.append(tuple(argv))
        expected, result = self.responses.popleft()
        assert tuple(argv) == tuple(expected)
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, Completed) else Completed(0, result)


class FakeVPN:
    def __init__(self, states=None, delay=0):
        self.states = deque(states or [])
        self.current = Status(State.DISCONNECTED, logged_in=True)
        self.calls = []
        self.delay = delay
        self.on_connect = None
        self.status_calls = 0
        self.started = asyncio.Event()

    async def status(self):
        self.status_calls += 1
        if self.states:
            self.current = self.states.popleft()
        return self.current

    async def connect(self, candidate):
        self.calls.append(("connect", candidate.key))
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.on_connect:
            self.on_connect(candidate)
        else:
            self.current = Status(
                State.CONNECTED,
                candidate.location.city,
                candidate.protocol,
                candidate.port,
                logged_in=True,
            )

    async def disconnect(self):
        self.calls.append(("disconnect",))
        self.current = Status(State.DISCONNECTED, logged_in=True)

    async def locations(self):
        return []

    async def capabilities(self):
        return {"stealth": [443]}


class FakeProbe:
    def __init__(self, metrics=None, delay=0):
        self.metrics = metrics or Metrics(rtt_ms=30, loss_pct=0, jitter_ms=2, https_ok=True)
        self.delay = delay
        self.calls = 0
        self.cancelled = False
        self.started = asyncio.Event()

    async def measure(self, profile):
        self.calls += 1
        self.started.set()
        try:
            await asyncio.sleep(self.delay)
            return replace(self.metrics)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
