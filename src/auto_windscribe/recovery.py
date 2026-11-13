"""Recovery under the controller's operation lock; no speculative connection cycles."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass

from .errors import AppError
from .health import NetworkHealthChecker
from .models import Candidate, Profile, State
from .windscribe import VPN


@dataclass
class RecoveryReport:
    safe: bool = False
    phase: str = "RECOVERY_PENDING"
    checks: int = 0
    reason: str = "Not attempted"
    health: dict | None = None


class GUICompatibility:
    warning = (
        "Manual protocol attempts may trigger Windscribe's Switch to Auto dialog. "
        "No supported suppression is available. Leave GUI idle during scans."
    )

    @staticmethod
    def matches(status: object, candidate: Candidate) -> bool:
        from .engine import owns

        return owns(status, candidate)  # type: ignore[arg-type]


class NetworkRecoveryManager:
    def __init__(
        self,
        vpn: VPN,
        health: NetworkHealthChecker,
        disconnect: Callable[[Candidate], Awaitable[None]],
    ) -> None:
        self.vpn, self.health, self.disconnect = vpn, health, disconnect
        self.last = RecoveryReport()

    async def recover(
        self,
        profile: Profile,
        candidate: Candidate | None = None,
        owned: bool = False,
        unresolved: bool = False,
    ) -> RecoveryReport:
        self.last = RecoveryReport(reason="Waiting for native state and baseline")
        try:
            async with asyncio.timeout(profile.recovery_timeout):
                for count in range(profile.max_recoveries):
                    self.last.checks = count + 1
                    status = await self.vpn.status()
                    if status.state == State.CONNECTED:
                        if (
                            not owned
                            or candidate is None
                            or not GUICompatibility.matches(status, candidate)
                        ):
                            self.last.reason = "External or unowned tunnel; no disconnect sent"
                            return self.last
                        await self.disconnect(candidate)
                        owned = False
                    elif status.state in (State.CONNECTING, State.DISCONNECTING, State.UNKNOWN):
                        self.last.reason = (
                            "Native state remains unsettled; no competing request sent"
                        )
                        await asyncio.sleep(profile.recovery_delay)
                        continue
                    if unresolved:
                        self.last.reason = "IPC request unresolved; inspect official app"
                        return self.last
                    await asyncio.sleep(profile.recovery_delay)
                    health = await self.health.check(profile)
                    self.last.health = asdict(health)
                    # Recheck for delayed/external transitions after health.
                    final = await self.vpn.status()
                    if health.safe and final.state == State.DISCONNECTED:
                        self.last.safe = True
                        self.last.phase = "BASELINE_HEALTHY"
                        self.last.reason = "Disconnected baseline verified; scan may continue"
                        return self.last
                    self.last.reason = "Baseline not restored; inspect official network tools"
        except AppError:
            self.last.reason = "Native CLI or health check unavailable; inspect official app"
        except TimeoutError:
            self.last.reason = "Recovery deadline expired; native state retained"
        self.last.phase = "RECOVERY_FAILED"
        return self.last
