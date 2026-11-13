"""Low-volume baseline observations. Never changes routes, DNS or firewall policy."""

import asyncio
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from urllib.parse import urlsplit

from .errors import AppError
from .models import Profile, State, Status
from .process import Runner
from .safety import SafetyGate
from .windscribe import VPN


class HealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DNS_FAILURE = "DNS_FAILURE"
    HTTPS_UNAVAILABLE = "HTTPS_UNAVAILABLE"
    NO_CONNECTIVITY = "NO_CONNECTIVITY"
    VPN_FIREWALL_BLOCKING = "VPN_FIREWALL_BLOCKING"
    RECOVERY_PENDING = "RECOVERY_PENDING"
    UNKNOWN = "UNKNOWN"


@dataclass
class HealthReport:
    state: HealthState
    native: str
    firewall: str
    dns: bool | None = None
    ping: bool | None = None
    google_https: bool | None = None
    independent_https: bool | None = None
    route: str = "unknown"
    detail: str = ""

    @property
    def safe(self) -> bool:
        return (
            self.native == "disconnected"
            and self.state in (HealthState.HEALTHY, HealthState.DEGRADED)
            and self.dns is True
            and self.google_https is True
            and self.independent_https is True
        )


def classify(
    status: Status,
    dns: bool | None,
    ping: bool | None,
    google: bool | None,
    independent: bool | None,
    route: str,
) -> HealthReport:
    if status.state != State.DISCONNECTED:
        state = HealthState.RECOVERY_PENDING
    elif dns and google and independent:
        state = HealthState.HEALTHY if ping else HealthState.DEGRADED
    elif dns is False and independent:
        state = HealthState.DNS_FAILURE
    elif ping and not google and not independent:
        state = HealthState.HTTPS_UNAVAILABLE
    elif any(v is None for v in (dns, ping, google, independent)):
        state = HealthState.UNKNOWN
    elif not any((dns, ping, google, independent)):
        state = HealthState.NO_CONNECTIVITY
    else:
        state = HealthState.DEGRADED
    # Firewall On is correlation, not proof it blocked packets. Never infer causality.
    detail = "Firewall state is observational; blocking cause unproven."
    if status.state == State.DISCONNECTED and status.firewall.casefold() in ("on", "always on"):
        detail = "Firewall enabled while disconnected; blocking may be policy. No bypass performed."
    return HealthReport(
        state,
        status.state.value,
        status.firewall,
        dns,
        ping,
        google,
        independent,
        route,
        detail,
    )


class NetworkHealthChecker:
    def __init__(self, runner: Runner, vpn: VPN, gate: SafetyGate) -> None:
        self.runner, self.vpn, self.gate = runner, vpn, gate

    async def check(self, profile: Profile) -> HealthReport:
        self.gate.require_live()
        profile.validate()
        before = await self.vpn.status()

        async def success(argv: list[str], https: bool = False) -> bool | None:
            try:
                response = await self.runner.run(argv, timeout=6)
                return response.returncode == 0 and (
                    not https or response.stdout.strip() in ("200", "204")
                )
            except AppError:
                return None

        def curl(url: str) -> list[str]:
            return [
                "/usr/bin/curl",
                "-q",
                "--silent",
                "--noproxy",
                "*",
                "--proto",
                "=https",
                "--connect-timeout",
                "3",
                "--max-time",
                "5",
                "--max-filesize",
                "65536",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                url,
            ]

        async def route_kind() -> str:
            try:
                r = await self.runner.run(
                    ["/usr/sbin/ip", "-j", "route", "show", "default"], timeout=3
                )
                rows = json.loads(r.stdout)
                return (
                    "present" if r.returncode == 0 and isinstance(rows, list) and rows else "absent"
                )
            except (AppError, ValueError):
                return "unknown"

        async with asyncio.TaskGroup() as group:
            dns = group.create_task(success(["/usr/bin/getent", "ahosts", "google.com"]))
            ping = group.create_task(
                success(["/usr/bin/ping", "-n", "-c", "2", "-W", "2", "google.com"])
            )
            google = group.create_task(success(curl("https://www.google.com/generate_204"), True))
            independent = group.create_task(success(curl(profile.health_endpoint), True))
            route = group.create_task(route_kind())
        after = await self.vpn.status()
        report = classify(
            after,
            dns.result(),
            ping.result(),
            google.result(),
            independent.result(),
            route.result(),
        )
        if before.state != after.state or before.firewall != after.firewall:
            report.state = HealthState.RECOVERY_PENDING
            report.detail = "Native state or firewall changed during health check."
        return report

    @staticmethod
    def plan(profile: Profile) -> dict:
        return {
            "google_ping": "ping -n -c 2 -W 2 google.com",
            "dns": "google.com",
            "https": ["https://www.google.com/generate_204", profile.health_endpoint],
            "route": "read-only default route presence",
            "executed": False,
        }


def validate_endpoint(url: str) -> None:
    if not isinstance(url, str):
        raise ValueError("Health endpoint must be a URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Health endpoint must be HTTPS without credentials, query or custom port")


def public_report(report: HealthReport) -> dict:
    return asdict(report)
