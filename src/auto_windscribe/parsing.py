"""Parsers for the native 2.25.8 CLI; no substring-based success detection."""

import re
import statistics

from .errors import ParseError
from .models import PROTOCOLS, Location, Metrics, State, Status

ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def clean(text: str) -> str:
    return ANSI.sub("", text)


def parse_status(text: str) -> Status:
    fields = {}
    for line in clean(text).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            key = key.strip().lower()
            if key in fields:
                raise ParseError("duplicate status field")
            fields[key] = value.strip()
    connection = fields.get("connection", "")
    tunnel_check = "unknown"
    for suffix, check in (
        (" (checking the connection)", "checking"),
        (" (network interference detected)", "interference"),
    ):
        if connection.endswith(suffix):
            connection = connection[: -len(suffix)]
            tunnel_check = check
    diagnostic = ""
    if connection.startswith("Error:"):
        # Known CLI errors represent disconnected states. Never retain raw details.
        errors = {
            "Location does not exist or is disabled": "location_unavailable",
            "The VPN tunnel could not be established": "tunnel_failed",
            "WireGuard adapter setup failed": "adapter_failed",
            "Could not retrieve WireGuard configuration": "configuration_unavailable",
            "The server did not accept the connection credentials": "authentication_failed",
            "You are out of data": "account_unavailable",
            "Unable to start custom DNS service": "dns_service_failed",
        }
        diagnostic = next((code for phrase, code in errors.items() if phrase in connection), "")
        if diagnostic:
            connection = "Disconnected"
    if connection.startswith("Disconnected due to reaching WireGuard key limit"):
        connection, diagnostic = "Disconnected", "wireguard_key_limit"
    match = re.fullmatch(
        r"(Disconnected|Connecting|Connected|Disconnecting)(?: to (.+))?", connection, re.I
    )
    if not match:
        return Status()
    state = State(match[1].lower())
    location = match[2] or ""
    protocol, port = "", None
    proto_match = re.fullmatch(r"([A-Za-z]+):(\d{1,5})", fields.get("protocol", ""))
    if proto_match and proto_match[1].lower() in PROTOCOLS:
        protocol, port = proto_match[1].lower(), int(proto_match[2])
        if not 1 <= port <= 65535:
            raise ParseError("protocol port")
    login = fields.get("login", "").lower()
    return Status(
        state,
        location,
        protocol,
        port,
        fields.get("firewall", "unknown").lower(),
        True if login == "logged in" else False if login == "logged out" else None,
        fields.get("internet", "unknown").lower(),
        diagnostic,
        tunnel_check,
    )


def parse_ports(text: str) -> list[int]:
    value = clean(text).strip()
    if not re.fullmatch(r"\d{1,5}(?:\s*,\s*\d{1,5})*", value):
        raise ParseError("ports")
    ports = list(dict.fromkeys(int(p.strip()) for p in value.split(",")))
    if any(not 1 <= p <= 65535 for p in ports):
        raise ParseError("ports")
    return ports


def parse_protocols(text: str) -> list[str]:
    for line in text.splitlines():
        match = re.match(r"\s*protocol\s+(.+)", line)
        if match:
            found = [p.strip() for p in match[1].split(",")]
            result = [p for p in found if p in PROTOCOLS]
            if result:
                return result
    raise ParseError("protocol help")


def parse_locations(text: str) -> list[Location]:
    locations = {}
    for line in clean(text).splitlines():
        line = line.strip()
        if not line or line.startswith("Best Location - "):
            continue
        tags = []
        while match := re.search(r"\s+\((Pro|Free|Disabled|10 Gbps)\)$", line, re.I):
            tags.append(match[1].lower())
            line = line[: match.start()]
        parts = [p.strip() for p in line.split(" - ", 2)]
        if len(parts) != 3 or any(not p or len(p) > 160 or not p.isprintable() for p in parts):
            raise ParseError("location catalog")
        region, city, nickname = parts
        # CLI groups US/Canada by region. Virtual regions retain their published names.
        country = (
            "United States"
            if region.startswith("US ")
            else ("Canada" if region.startswith("Canada ") else region)
        )
        # The Pro account's CLI may omit (Pro). Absence is NOT proof of Free access.
        tier = "pro" if "pro" in tags else "free" if "free" in tags else "unknown"
        loc = Location(region, city, nickname, not ({"disabled", "pro"} & set(tags)), tier, country)
        if loc.key in locations:
            raise ParseError("duplicate location")
        locations[loc.key] = loc
    if not locations:
        raise ParseError("empty location catalog")
    return list(locations.values())


def parse_ping(text: str) -> Metrics:
    text = clean(text)
    match = re.search(r"(\d+) packets transmitted,\s*(\d+) (?:packets )?received", text)
    if not match:
        raise ParseError("ping summary")
    sent, received = int(match[1]), int(match[2])
    if sent <= 0 or received > sent:
        raise ParseError("ping counts")
    replies = []
    for line in text.splitlines():
        if "ttl=" not in line.lower():
            continue
        reply = re.search(r"time([=<])(\d+(?:\.\d+)?)\s*ms", line)
        if reply and reply[1] == "=":  # An upper bound is not an exact measurement.
            replies.append(float(reply[2]))
    rtt = statistics.mean(replies) if replies else None
    jitter = statistics.pstdev(replies) if len(replies) >= 2 else None
    return Metrics(rtt_ms=rtt, loss_pct=(sent - received) / sent * 100, jitter_ms=jitter)
