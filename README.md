# Auto-Windscribe 0.2

A terminal VPN manager and adaptive connection analyzer for **Linux Bluefin** using the existing
`/usr/bin/windscribe-cli`. This is an unofficial controller, not a VPN client or a Windscribe product.
The official application owns your account, system tunnel, firewall, and desktop preferences.

[فارسی](README.fa.md) · [Installation](docs/INSTALL.md) · [Architecture](docs/ARCHITECTURE.md)
· [Capabilities](docs/CAPABILITIES.md) · [Troubleshooting](docs/TROUBLESHOOTING.md)

## Start

Requires Linux x86_64, Python 3.12+, and the installed, authenticated Windscribe desktop package.
Developed for Bluefin / Fedora 44 and native Windscribe CLI 2.25.8. No root, host package layering,
new VPN engine, daemon, or system service is needed.

```bash
uv tool install --python 3.12 git+https://github.com/NullRoute-Lab/Auto-Windscribe.git
auto-windscribe
```

When disconnected, the TUI offers Turbo Discovery. **Press Enter once to confirm the bounded scan.**
That confirmation covers its preflight budget and sequential VPN attempts. Stop at any time.
An existing connection is protected: explicitly disconnect it before scanning or selecting another
endpoint. Quit with **Q** to retain the system-wide VPN connection, including a successful result.
There is no persistent live lock, configuration editing requirement, or per-candidate confirmation.

The application is ready for deliberate live use. Development agents have a separate
[offline-only testing boundary](docs/TEST_AUTHORIZATION.md); installing or publishing the app does
not authorize an agent to accept its prompts. Live success on the target network is still unproven.

## Turbo Discovery

1. **Discover:** use the official CLI for locations and supported ports of WireGuard, OpenVPN UDP,
   OpenVPN TCP, Stealth, and WStunnel. Generate every selected supported combination; exclude IKEv2.
2. **Preflight:** a bounded async scheduler supports trusted endpoint DNS caching and TCP connects,
   deduplication, cancellation, rate limits, retries and total probe budgets. Default concurrency 16,
   TCP deadline 1.5 seconds. TCP evidence is displayed separately from verified VPN Ping.
3. **Verify:** choose diverse protocols, countries, locations and ports using history and available
   preflight evidence. Dispatch exactly one native VPN attempt at a time, then verify native state,
   tunnel routing and tunnel-bound HTTPS. Record quality only when measured.

**Current endpoint limitation:** CLI 2.25.8 exports no node addresses or protocol-to-address mapping.
Official upstream uses authenticated PortMap data to select `ip`, `ip2`, or `ip3`. This controller
neither reads credentials nor guesses addresses. Its native inventory adapter therefore reports
**preflight inconclusive / endpoint unavailable**, without issuing TCP/DNS probes. The real TCP
backend is implemented and tested through injected trusted inventories; it is not enabled for
unverified addresses. There is no arbitrary-address scan option. Turbo continues with adaptive
native verification. See [research and limits](docs/TURBO.md).

TCP success is never VPN authentication, HTTPS success, or VPN Ping. UDP/WireGuard candidates remain
inconclusive until native handshakes; UDP silence is never an “open” result. TLS preflight is omitted
because the desktop application's effective SNI and protocol framing are not safely observable.

| Profile | Native attempts per session | Establishment ceiling | Session budget | Behavior |
| --- | ---: | ---: | ---: | --- |
| **turbo** (default) | 12 | 20 s | 300 s | Stop on first HTTPS-verified success; shorten deadlines for previously fast successes |
| **balanced** | 36 | 30 s | 1,200 s | Broader diverse search with quality measurements and ranking |
| **exhaustive** | 100 | 35 s | 3,600 s | Preserve the entire selected matrix; resume bounded sessions until complete |

A failure does not restart the scan. Completed combinations stay checkpointed. Unknown or conflicting
native state pauses the scan for diagnosis. A timed-out attempt permits continuation only when the
last completed status poll positively reports disconnected. Pause/Quit never sends disconnect, so
an already-dispatched native request may still finish. Resume requires disconnected state and a
new session confirmation. No hidden retries of uncertain operations.

## Terminal controls

| Key | Action |
| --- | --- |
| `/`, Ctrl+F | Fuzzy search country, region, city, nickname |
| F | Country/city/nickname/tier filters |
| P | Protocol/port filters and application budgets; blank ports selects all supported ports |
| S | Save/edit application profiles and network/settings context labels |
| Profile dropdown | Turbo, Balanced, Exhaustive or saved profile; pause before changing |
| Space | Mark exact location/protocol/port combinations for a targeted scan |
| Enter | Confirm startup scan or connect the highlighted combination |
| R | Rescan filtered/marked candidates, with one confirmation |
| Resume button | Resume most recent pending/paused/limited scan with its saved profile |
| X / Pause–Stop | Cancel app work and checkpoint; retain current VPN state |
| D | Confirm system-wide disconnect |
| O / column headers | Sort; dropdown also provides loss, reliability, connection time, recency |
| U | Refresh official catalog; fall back to last valid cache with an outdated warning |
| A | Toggle the launch scan offer; always requires consent |
| `?`, F1 | Help |
| Esc | Close dialog / return to table |
| Q, Ctrl+C | Quit while keeping the system VPN |

Large inventories show the first 1,000 sorted matches; search and filters access every candidate.
The scan inventory itself is not truncated.

The header shows READY / SCANNING and native connection state. Tables separate preflight evidence
and TCP milliseconds from VPN Ping. The leaderboard shows verified RTT, jitter, loss, establishment
time and historical success ratio. Diagnostics explain phase errors without printing raw CLI logs
or addresses. Session ETA appears only after three completed attempts and describes observed pace,
not a prediction that a working tunnel will be found.

## CLI

Interactive commands ask once; scripts require `--live`. Read-only commands never probe connectivity.
`--auto-scan --live` explicitly consents to the TUI's bounded startup session when disconnected;
ordinary launches never silently change the network.

```bash
auto-windscribe status
auto-windscribe doctor
auto-windscribe locations --search Amsterdam
auto-windscribe locations --countries
auto-windscribe protocols
auto-windscribe scan --profile turbo
auto-windscribe scan --profile balanced --country Netherlands --max-tests 8
auto-windscribe scan --profile exhaustive --protocol stealth --session-budget 600
auto-windscribe scan --resume 1
auto-windscribe connect Stroopwafel --protocol stealth --port 443
auto-windscribe disconnect
auto-windscribe results --sort rtt
auto-windscribe results --scans
auto-windscribe profiles show turbo
auto-windscribe --help
```

The named server is an example; confirm it is currently available in your CLI catalog.
For an explicitly bounded scripted session:

```bash
auto-windscribe scan --profile turbo --max-tests 3 --preflight-budget 16 --session-budget 120 --live
```

Existing-connection replacement additionally requires `connect --interrupt`; a scan always refuses
to replace a preexisting tunnel. Balanced/Exhaustive may disconnect only their own matched tunnel
between candidates. Leave the official GUI idle while scanning; CLI status cannot distinguish two
GUI actions to the same city/protocol/port.

## Offline use and testing

```bash
auto-windscribe --demo                         # synthetic adapters, temporary data only
auto-windscribe --offline                      # cached catalog and historical observations
auto-windscribe scan --profile turbo --dry-run --json
uv sync --frozen
uv run ruff check src tests benchmarks
uv run ruff format --check src tests benchmarks
uv run mypy
uv run pytest -m 'not live' --cov=auto_windscribe
uv run python -m benchmarks.preflight
uv build
```

`--dry-run` and `--offline` never call the native CLI or contact endpoints and do not change persistent
application files. Planning needs an existing CLI catalog cache; no fabricated fallback is supplied.
`--offline --live` cannot override offline mode. All tests block real subprocess creation, DNS and
socket connects; synthetic TUI tests exercise the real widgets and controller with fake adapters.

Measured mock benchmark: **3.206851 s sequential vs 0.414847 s with concurrency capped at 16
(7.73×)**, medians of three runs for 32 synthetic 100 ms endpoints. The 100-starts/s rate limit kept
observed peak concurrency at 10. These are scheduler measurements, **not real VPN speedups**.
[Raw measurements](docs/benchmark-v0.2.json) and [methodology](docs/TURBO.md).

## Preferences, privacy, and updates

The app does not change GUI-only AmneziaWG presets, Anti-Censorship, SNI, routing, DNS, split
tunneling, IPv6, MTU or TLS padding. Change those in the official app yourself, then give the profile
a new non-sensitive `settings_label` to separate observations. CLI connection requests can update
the official client's remembered endpoint/protocol; no internal configuration is edited.

Profiles contain only application preferences, never credentials. SQLite history, caches and config
use the `auto-windscribe` subdirectories of XDG_CONFIG_HOME, XDG_DATA_HOME and XDG_CACHE_HOME.
Missing configuration defaults to Turbo and a launch confirmation offer. Old `live_enabled` fields
are ignored. Optional download measurement remains disabled by default.

```bash
uv tool upgrade auto-windscribe
uv tool uninstall auto-windscribe
```

Local-wheel installs should first switch to the Git source as described in [INSTALL](docs/INSTALL.md).
Uninstall retains application history and never removes Windscribe. Database schema v2 migrates v1;
back up application data before downgrading. No public endpoint IPs, personal logs or runtime data
are committed. MIT license; original implementation with [reference acknowledgments](THIRD_PARTY_NOTICES.md).
