"""Application composition and catalog refresh with last-valid fallback."""

import time
from dataclasses import dataclass

from .engine import Controller
from .errors import AppError
from .health import NetworkHealthChecker
from .models import Candidate, Location, Profile, Status
from .preflight import NativeInventory, Preflight, TCPTransport
from .probes import TunnelProbe
from .process import AsyncRunner
from .safety import OperationLock, SafetyGate
from .storage import Catalog, Config, Paths, Profiles, Store
from .windscribe import VPN, WindscribeCLI


async def refresh_catalog(vpn: VPN, paths: Paths, ttl: int, persist: bool = True) -> Catalog:
    try:
        locations = await vpn.locations()
        capabilities = await vpn.capabilities()
        if not locations or not capabilities:
            raise AppError("empty_catalog", "Windscribe returned an empty catalog.")
        catalog = Catalog(locations, capabilities, time.time())
        if persist:
            catalog.save(paths.cache / "catalog.json")
        return catalog
    except AppError as exc:
        cached = Catalog.load(paths.cache / "catalog.json", ttl)
        cached.stale = True
        cached.warning = f"Refresh failed ({exc.code}); using the last valid catalog."
        return cached


@dataclass
class Services:
    paths: Paths
    config: Config
    vpn: VPN
    store: Store
    profiles: Profiles
    controller: Controller
    offline: bool = False
    demo: bool = False
    readonly: bool = False

    async def catalog(self, refresh: bool = True) -> Catalog:
        if self.offline and not self.demo:
            return Catalog.load(self.paths.cache / "catalog.json", self.config.catalog_ttl)
        if refresh:
            return await refresh_catalog(
                self.vpn, self.paths, self.config.catalog_ttl, not self.readonly
            )
        return Catalog.load(self.paths.cache / "catalog.json", self.config.catalog_ttl)

    def profile(self, name: str | None = None) -> Profile:
        return self.profiles.get(name or self.config.default_profile)


def build_services(
    paths: Paths, offline: bool = False, confirmed: bool = False, readonly: bool = False
) -> Services:
    if not offline and not readonly:
        paths.ensure()
    config = Config.load(paths)
    gate = SafetyGate(confirmed=confirmed, offline=offline)
    runner = AsyncRunner()
    vpn = WindscribeCLI(runner, gate) if not offline else OfflineVPN()
    store = Store(paths.data / "history.sqlite3", readonly=offline or readonly)
    controller = Controller(
        vpn,
        TunnelProbe(runner, gate),
        store,
        OperationLock(paths.data / "operation.lock"),
        gate,
        health=NetworkHealthChecker(runner, vpn, gate),
        preflight=Preflight(NativeInventory(), TCPTransport(gate), gate),
    )
    return Services(
        paths,
        config,
        vpn,
        store,
        Profiles(paths, readonly=offline or readonly),
        controller,
        offline,
        readonly=readonly or offline,
    )


class OfflineVPN:
    """Offline browsing has no route to the native executable."""

    async def status(self) -> Status:
        return Status()

    async def locations(self) -> list[Location]:
        raise AppError("offline", "Offline mode reads only the cached catalog.")

    async def capabilities(self) -> dict[str, list[int]]:
        raise AppError("offline", "Offline mode reads only cached capabilities.")

    async def connect(self, candidate: Candidate) -> None:
        raise AppError("offline", "Offline mode cannot connect.")

    async def disconnect(self) -> None:
        raise AppError("offline", "Offline mode cannot disconnect.")
