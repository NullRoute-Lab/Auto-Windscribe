"""Isolated, explicitly synthetic demo. Never used as a live catalog fallback."""

import asyncio
from dataclasses import replace

from .engine import Controller
from .models import Candidate, Location, Metrics, Profile, State, Status
from .safety import OperationLock, SafetyGate
from .service import Services
from .storage import Config, Paths, Profiles, Store


class DemoVPN:
    def __init__(self) -> None:
        self.current = Status(State.DISCONNECTED, logged_in=True, firewall="unchanged")
        self.calls: list[str] = []

    async def status(self) -> Status:
        return self.current

    async def locations(self) -> list[Location]:
        return [
            Location("Demo North", "Sample City", "Example One", country="Demo North"),
            Location("Demo South", "Test City", "Example Two", country="Demo South"),
        ]

    async def capabilities(self) -> dict[str, list[int]]:
        return {"wireguard": [443], "udp": [443], "tcp": [443], "stealth": [443], "wstunnel": [443]}

    async def connect(self, candidate: Candidate) -> None:
        self.calls.append("connect")
        self.current = Status(State.CONNECTING)
        await asyncio.sleep(0.03)
        self.current = Status(
            State.CONNECTED,
            candidate.location.nickname,
            candidate.protocol,
            candidate.port,
            "unchanged",
            True,
            "available",
        )

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.current = Status(State.DISCONNECTED, logged_in=True, firewall="unchanged")


class DemoProbe:
    async def measure(self, profile: Profile) -> Metrics:
        await asyncio.sleep(0.02)
        return Metrics(42, 0, 2, True, note="SIMULATED: these are not real measurements.")


class DemoGate(SafetyGate):
    def require_live(self) -> None:
        # This gate can only accompany the synthetic adapters in an isolated temporary store.
        return


def demo_services(paths: Paths) -> Services:
    paths.ensure()
    vpn, store, gate = DemoVPN(), Store(paths.data / "history.sqlite3"), DemoGate(offline=True)
    profiles = Profiles(paths)
    profiles.save(replace(Profile(), settle=0))
    profiles.save(replace(Profile(name="turbo"), settle=0))
    return Services(
        paths,
        Config(),
        vpn,
        store,
        profiles,
        Controller(
            vpn,
            DemoProbe(),
            store,
            OperationLock(paths.data / "operation.lock"),
            gate,
            poll_interval=0.01,
        ),
        offline=True,
        demo=True,
    )
