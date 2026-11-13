import pytest

from auto_windscribe.errors import ParseError
from auto_windscribe.models import State
from auto_windscribe.parsing import (
    parse_locations,
    parse_ping,
    parse_ports,
    parse_protocols,
    parse_status,
)
from tests.conftest import fixture_text


@pytest.mark.parametrize(
    "value,state",
    [
        ("Disconnected", State.DISCONNECTED),
        ("Disconnecting", State.DISCONNECTING),
        ("Connecting to Example", State.CONNECTING),
        ("Connected to Example", State.CONNECTED),
        ("Not connected", State.UNKNOWN),
        ("DISCONNECTED", State.DISCONNECTED),
        ("", State.UNKNOWN),
    ],
)
def test_state_exact(value, state):
    assert parse_status(f"Connection: {value}").state == state


def test_connected_fixture_no_ip_retained():
    status = parse_status(fixture_text("status_connected.txt"))
    assert status.state == State.CONNECTED
    assert (status.location, status.protocol, status.port) == ("Test City", "stealth", 443)
    assert status.firewall == "always on"
    assert "203.0.113" not in repr(status)


@pytest.mark.parametrize(
    "warning,check",
    [("checking the connection", "checking"), ("network interference detected", "interference")],
)
def test_connection_warning(warning, check):
    status = parse_status(f"Connection: Connected to Test City ({warning})")
    assert status.location == "Test City"
    assert status.tunnel_check == check


def test_failure_diagnostic():
    status = parse_status(
        "Connection: Error: The VPN tunnel could not be established. "
        "Please try a different protocol"
    )
    assert status.state == State.DISCONNECTED
    assert status.diagnostic == "tunnel_failed"
    assert parse_status("Connection: Error: undocumented detail").state == State.UNKNOWN


def test_duplicate_status_refused():
    with pytest.raises(ParseError):
        parse_status("Connection: Connected\nConnection: Disconnected")


@pytest.mark.parametrize(
    "text", ["", "error 443", "0, 443", "65536", "443 80", "443, fake", "version 2.25.8"]
)
def test_bad_ports(text):
    with pytest.raises(ParseError):
        parse_ports(text)


def test_ports_and_protocols():
    assert parse_ports("443, 80, 443, 53\n") == [443, 80, 53]
    assert parse_protocols("  protocol     wireguard, udp, tcp, stealth, wstunnel") == [
        "wireguard",
        "udp",
        "tcp",
        "stealth",
        "wstunnel",
    ]
    with pytest.raises(ParseError):
        parse_protocols("unrecognized help")


def test_locations_real_format_no_inferred_tier():
    locations = parse_locations(fixture_text("locations.txt"))
    assert len(locations) == 4
    assert locations[0].tier == "unknown"
    assert locations[1].tier == "pro"
    assert not locations[1].available  # Pro tag is CLI's inaccessible premium marker.
    assert locations[1].country == "Canada"
    assert not locations[2].available
    assert locations[3].tier == "free"


@pytest.mark.parametrize("text", ["", "No locations.", "US - city", "US -  - nick", "error"])
def test_invalid_locations(text):
    with pytest.raises(ParseError):
        parse_locations(text)


def test_malformed_catalog_cannot_partially_replace_cache():
    with pytest.raises(ParseError):
        parse_locations("US East - City - Nick\ncorrupted row")


def test_ping_loss_rtt_jitter():
    metrics = parse_ping(fixture_text("ping.txt"))
    assert metrics.rtt_ms == 30
    assert metrics.loss_pct == 25
    assert metrics.jitter_ms == pytest.approx(8.1649658)


def test_blocked_ping_is_unknown_rtt():
    metrics = parse_ping("4 packets transmitted, 0 received, 100% packet loss")
    assert metrics.rtt_ms is None and metrics.jitter_ms is None
    assert metrics.loss_pct == 100


def test_ping_upper_bound_not_exact():
    metrics = parse_ping("64 bytes ttl=50 time<1 ms\n1 packets transmitted, 1 received")
    assert metrics.rtt_ms is None
    assert metrics.loss_pct == 0


@pytest.mark.parametrize(
    "text", ["no output", "0 packets transmitted, 0 received", "2 packets transmitted, 3 received"]
)
def test_invalid_ping(text):
    with pytest.raises(ParseError):
        parse_ping(text)
