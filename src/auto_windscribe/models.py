"""Typed domain objects. Missing measurements are None, never invented zeros."""

import json
import re
import time
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Any

PROTOCOLS = ("wireguard", "udp", "tcp", "stealth", "wstunnel")


class State(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Status:
    state: State = State.UNKNOWN
    location: str = ""
    protocol: str = ""
    port: int | None = None
    firewall: str = "unknown"
    logged_in: bool | None = None
    internet: str = "unknown"
    diagnostic: str = ""
    tunnel_check: str = "unknown"


@dataclass(frozen=True)
class Location:
    region: str
    city: str
    nickname: str
    available: bool = True
    tier: str = "unknown"
    country: str = ""

    def __post_init__(self) -> None:
        for name in (self.region, self.city, self.nickname):
            if not isinstance(name, str) or not name or len(name) > 160 or not name.isprintable():
                raise ValueError("Invalid catalog location name")
        if (
            not isinstance(self.country, str)
            or len(self.country) > 160
            or (self.country and not self.country.isprintable())
            or type(self.available) is not bool
            or self.tier not in ("free", "pro", "unknown")
        ):
            raise ValueError("Invalid catalog location metadata")

    @property
    def key(self) -> str:
        return " / ".join((self.region, self.city, self.nickname))

    @property
    def target(self) -> str:
        return self.nickname

    @property
    def search_text(self) -> str:
        return f"{self.country} {self.region} {self.city} {self.nickname}"


@dataclass(frozen=True)
class Candidate:
    location: Location
    protocol: str
    port: int

    def __post_init__(self) -> None:
        if (
            self.protocol not in PROTOCOLS
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
        ):
            raise ValueError("Invalid protocol or port")
        if not self.location.target or self.location.target.startswith("-"):
            raise ValueError("Invalid location nickname")
        if any(ord(c) < 32 for c in self.location.target):
            raise ValueError("Invalid location nickname")

    @property
    def key(self) -> str:
        return f"{self.location.key} / {self.protocol}:{self.port}"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Candidate":
        return cls(Location(**data["location"]), data["protocol"], data["port"])


@dataclass
class Metrics:
    rtt_ms: float | None = None
    loss_pct: float | None = None
    jitter_ms: float | None = None
    https_ok: bool | None = None
    download_mbps: float | None = None
    note: str = ""


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass
class Result:
    candidate: Candidate
    outcome: Outcome
    code: str
    detail: str
    connect_s: float | None = None
    metrics: Metrics = field(default_factory=Metrics)
    timestamp: float = field(default_factory=time.time)
    context: str = "default"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Result":
        return cls(
            **{
                **data,
                "candidate": Candidate.from_dict(data["candidate"]),
                "outcome": Outcome(data["outcome"]),
                "metrics": Metrics(**data["metrics"]),
            }
        )


@dataclass
class Profile:
    name: str = "default"
    protocols: list[str] = field(default_factory=lambda: list(PROTOCOLS))
    ports: dict[str, list[int]] = field(default_factory=dict)
    countries: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    tier: str = "all"
    max_tests: int = 12
    timeout: float = 35.0
    settle: float = 2.0
    interval: float = 2.0
    retries: int = 0
    cooldown: float = 3600.0
    mode: str = "first-success"
    ping_count: int = 4
    download: bool = False
    network_label: str = "default"
    settings_label: str = "gui-unchanged"

    def validate(self) -> "Profile":
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", self.name):
            raise ValueError("Profile name must be 1–48 letters, digits, hyphens or underscores")
        if (
            not isinstance(self.protocols, list)
            or not self.protocols
            or any(p not in PROTOCOLS for p in self.protocols)
            or len(set(self.protocols)) != len(self.protocols)
        ):
            raise ValueError("Choose unique supported Linux protocols")
        if not isinstance(self.ports, dict):
            raise ValueError("Ports must map protocol names to integer lists")
        for proto, ports in self.ports.items():
            if (
                proto not in PROTOCOLS
                or not isinstance(ports, list)
                or not ports
                or any(type(p) is not int or not 1 <= p <= 65535 for p in ports)
            ):
                raise ValueError("Ports must be nonempty lists of integers from 1 to 65535")
        for key in ("countries", "cities", "locations"):
            values = getattr(self, key)
            if not isinstance(values, list) or any(
                not isinstance(v, str) or not v or len(v) > 160 or not v.isprintable()
                for v in values
            ):
                raise ValueError(f"{key} must be a list of short names")
        bounds = {
            "max_tests": (1, 500),
            "timeout": (3, 300),
            "settle": (0, 30),
            "interval": (1, 60),
            "retries": (0, 2),
            "cooldown": (0, 604800),
            "ping_count": (0, 10),
        }
        for key, (low, high) in bounds.items():
            value = getattr(self, key)
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
        for key in ("max_tests", "retries", "ping_count"):
            if type(getattr(self, key)) is not int:
                raise ValueError(f"{key} must be an integer")
        if self.mode not in ("first-success", "rank", "fast"):
            raise ValueError("Mode must be first-success, rank, or fast")
        if self.tier not in ("all", "free", "pro", "unknown"):
            raise ValueError("Invalid account tier filter")
        if type(self.download) is not bool:
            raise ValueError("download must be a boolean")
        for value in (self.network_label, self.settings_label):
            if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9 _-]{1,64}", value):
                raise ValueError("Context labels must be short, non-sensitive names")
        return self

    @property
    def context(self) -> str:
        # No SSID, IP address, ISP lookup, or internal Windscribe configuration reads.
        data = [
            self.network_label,
            self.settings_label,
            self.ping_count,
            self.download,
            self.timeout,
            self.settle,
            "probe-v1",
        ]
        return sha256(json.dumps(data).encode()).hexdigest()[:20]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        try:
            return cls(**data).validate()
        except TypeError as exc:
            raise ValueError("Invalid profile fields") from exc


def builtin_profiles() -> dict[str, Profile]:
    return {
        "default": Profile(),
        "restrictive": Profile(
            name="restrictive",
            protocols=["stealth", "wstunnel", "tcp", "wireguard", "udp"],
            timeout=50,
        ),
        "fast": Profile(name="fast", mode="fast", max_tests=5, ping_count=0),
        "ranking": Profile(name="ranking", mode="rank", max_tests=20),
    }


def encode(obj: Any) -> str:
    return json.dumps(asdict(obj), ensure_ascii=False, separators=(",", ":"))
