"""Recovery under the controller's operation lock; no speculative connection cycles."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field

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
    settlement_resolved: bool = False
    timeline: list[dict] = field(default_factory=list)
    elapsed_s: float = 0.0


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
        self.emit: Callable[[RecoveryReport], None] = lambda report: None

    async def recover(
        self,
        profile: Profile,
        candidate: Candidate | None = None,
        owned: bool = False,
        unresolved: bool = False,
    ) -> RecoveryReport:
        self.last = RecoveryReport(
            phase="WAITING_FOR_NATIVE",
            reason="Waiting for Windscribe to finish the previous connection attempt",
        )
        started = time.monotonic()
        native_deadline = started + profile.settlement_timeout
        interval = profile.settlement_poll
        stable_since: float | None = None
        previous: tuple[str, str] | None = None
        self.emit(self.last)
        try:
            async with asyncio.timeout(profile.recovery_timeout):
                while True:
                    if time.monotonic() >= native_deadline:
                        self.last.reason = "Native settlement deadline exhausted; queue retained"
                        break
                    try:
                        async with asyncio.timeout(
                            min(profile.status_timeout, native_deadline - time.monotonic())
                        ):
                            status = await self.vpn.status()
                    except (AppError, TimeoutError):
                        stable_since = None
                        self.last.phase = "UNSAFE_STATE"
                        self.last.reason = (
                            "Native status unavailable; observing within settlement budget"
                        )
                        self.emit(self.last)
                        await asyncio.sleep(interval)
                        continue
                    self.last.elapsed_s = time.monotonic() - started
                    event = (status.state.value, status.firewall)
                    if event != previous:
                        self.last.timeline.append(
                            {
                                "seconds": round(self.last.elapsed_s, 3),
                                "native": status.state.value,
                                "firewall": status.firewall,
                            }
                        )
                        self.last.timeline[:] = self.last.timeline[-256:]
                        previous = event
                        stable_since = None
                        interval = profile.settlement_poll
                    if status.state == State.CONNECTED:
                        if (
                            not owned
                            or candidate is None
                            or not GUICompatibility.matches(status, candidate)
                        ):
                            self.last.phase = "EXTERNAL_CONNECTION_DETECTED"
                            self.last.reason = "Unowned or mismatched tunnel; no disconnect sent"
                            self.emit(self.last)
                            return self.last
                        await self.disconnect(candidate)
                        owned = False
                        stable_since = None
                    elif status.state == State.DISCONNECTED:
                        if unresolved:
                            self.last.phase = "UNSAFE_STATE"
                            self.last.reason = (
                                "IPC dispatch has no terminal acknowledgment; queue retained"
                            )
                            self.emit(self.last)
                            return self.last
                        if stable_since is None:
                            stable_since = time.monotonic()
                        if time.monotonic() - stable_since >= profile.recovery_delay:
                            self.last.phase = "BASELINE_CHECKING"
                            self.last.checks += 1
                            self.emit(self.last)
                            report = await self.health.check(profile)
                            self.last.health = asdict(report)
                            final = await self.vpn.status()
                            if (
                                report.safe
                                and final.state == State.DISCONNECTED
                                and final.firewall == status.firewall == report.firewall
                            ):
                                self.last.safe = self.last.settlement_resolved = True
                                self.last.phase = "RECOVERED"
                                self.last.reason = (
                                    "Native settled and baseline verified; continue queue"
                                )
                                self.last.elapsed_s = time.monotonic() - started
                                self.emit(self.last)
                                return self.last
                            if self.last.checks >= profile.max_recoveries:
                                self.last.reason = (
                                    "Baseline verification budget exhausted; firewall preserved"
                                )
                                break
                            stable_since = None
                    else:
                        stable_since = None
                    self.last.phase = "NATIVE_SETTLING"
                    self.last.reason = (
                        "Waiting for Windscribe to finish the previous connection attempt"
                    )
                    self.emit(self.last)
                    await asyncio.sleep(min(interval, max(0, native_deadline - time.monotonic())))
                    interval = min(profile.settlement_poll_max, interval * 1.5)
        except asyncio.CancelledError:
            self.last.phase = "UNSAFE_STATE"
            self.last.reason = "Settlement cancelled; native state retained"
            self.emit(self.last)
            raise
        except AppError:
            self.last.reason = "Native control or baseline check failed; queue retained"
        except TimeoutError:
            self.last.reason = "Overall recovery deadline exhausted; queue retained"
        self.last.phase = "RECOVERY_EXHAUSTED"
        self.last.elapsed_s = time.monotonic() - started
        self.emit(self.last)
        return self.last
