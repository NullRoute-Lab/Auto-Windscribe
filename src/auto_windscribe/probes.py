"""Conservative Linux probes bound to a verified tunnel device, disabled by the gate."""

import json
import re
from typing import Protocol

from .errors import AppError, ParseError
from .models import Metrics, Profile
from .parsing import parse_ping
from .process import Runner
from .safety import SafetyGate


class Probe(Protocol):
    async def measure(self, profile: Profile) -> Metrics: ...


class TunnelProbe:
    def __init__(self, runner: Runner, gate: SafetyGate) -> None:
        self.runner, self.gate = runner, gate

    async def _interface(self) -> str:
        route = await self.runner.run(["/usr/sbin/ip", "-j", "route", "get", "1.1.1.1"])
        try:
            routes = json.loads(route.stdout)
            dev = routes[0]["dev"]
            if route.returncode or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,15}", dev):
                raise ValueError("Invalid route")
            link = await self.runner.run(["/usr/sbin/ip", "-j", "-d", "link", "show", "dev", dev])
            info = json.loads(link.stdout)[0]
            if link.returncode or info.get("linkinfo", {}).get("info_kind") not in (
                "tun",
                "wireguard",
            ):
                raise ValueError("Not a tunnel")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AppError(
                "tunnel_unverified",
                "The route to the probe is not a recognizable "
                "tun/WireGuard device. No traffic was probed.",
            ) from exc
        return dev

    async def measure(self, profile: Profile) -> Metrics:
        self.gate.require_live()
        dev = await self._interface()
        # Direct HTTPS IP avoids a separate DNS lookup. Proxies and curlrc are disabled.
        base = [
            "/usr/bin/curl",
            "-q",
            "--silent",
            "--noproxy",
            "*",
            "--proto",
            "=https",
            "--ipv4",
            "--interface",
            f"if!{dev}",
            "--connect-timeout",
            "5",
            "--max-time",
            "10",
            "--output",
            "/dev/null",
        ]
        https = await self.runner.run(
            [
                *base,
                "--max-filesize",
                "65536",
                "--write-out",
                "%{http_code}",
                "https://1.1.1.1/cdn-cgi/trace",
            ],
            timeout=12,
        )
        metrics = Metrics(https_ok=https.returncode == 0 and https.stdout.strip() == "200")
        if not metrics.https_ok:
            metrics.note = "Tunnel-bound HTTPS failed; endpoint blocking is also possible."
        if profile.ping_count:
            try:
                ping = await self.runner.run(
                    [
                        "/usr/bin/ping",
                        "-n",
                        "-I",
                        dev,
                        "-c",
                        str(profile.ping_count),
                        "-W",
                        "2",
                        "-w",
                        str(profile.ping_count * 3),
                        "1.1.1.1",
                    ],
                    timeout=profile.ping_count * 3 + 2,
                )
                if ping.returncode not in (0, 1):
                    raise ParseError("ping execution")
                parsed = parse_ping(ping.stdout)
                metrics.rtt_ms, metrics.loss_pct, metrics.jitter_ms = (
                    parsed.rtt_ms,
                    parsed.loss_pct,
                    parsed.jitter_ms,
                )
            except AppError:
                metrics.note += " ICMP unavailable; HTTPS remains the usability criterion."
        if profile.download and metrics.https_ok:
            # Explicit opt-in, one 1 MB transfer, no upload or automatic nearest-server lookup.
            # This hostname's DNS resolution uses the existing OS policy (documented limitation).
            download = await self.runner.run(
                [
                    *base,
                    "--max-filesize",
                    "1000000",
                    "--write-out",
                    "%{http_code} %{size_download} %{speed_download}",
                    "https://speed.cloudflare.com/__down?bytes=1000000",
                ],
                timeout=12,
            )
            try:
                code, size, speed = download.stdout.strip().split()
                if download.returncode == 0 and code == "200" and int(size) == 1000000:
                    rate = float(speed) * 8 / 1_000_000
                    if 0 < rate < 1_000_000:
                        metrics.download_mbps = rate
            except (ValueError, OverflowError):
                pass
            if metrics.download_mbps is None:
                metrics.note += " Optional bounded download unavailable."
        return metrics
