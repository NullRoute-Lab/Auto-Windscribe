# Troubleshooting

Start with `auto-windscribe doctor`, which reads native version/status/capabilities and lists
available AmneziaWG preset names. It performs no ping, HTTPS, speed or VPN connection test.
Use `doctor --offline` to avoid even native CLI calls.

| Diagnostic | Meaning / next step |
| --- | --- |
| `authorization_required` | App consent or explicit action consent is missing. Leave locked until the separately authorized testing phase |
| `offline_guard` | `--offline`, dry-run or `AUTO_WINDSCRIBE_OFFLINE=1` prohibits live operations |
| `cli_missing` | Existing `/usr/bin/windscribe-cli` was not found; inspect the installation without replacing packages automatically |
| `catalog_unavailable` | No valid cached catalog; use read-only `locations` to refresh through the existing native client |
| Outdated catalog | A refresh failed or the cached observation exceeded its TTL. Cached entries are not current availability promises |
| No eligible candidates | Filters/tier selection/cooldown removed all entries. On a Pro account, unmarked entries have unknown tier |
| `unsupported_port` | The port is absent from current capabilities; native silent fallback was prevented |
| `existing_connection` | A connection already exists or is changing. Review it; use explicit Disconnect before a scan |
| `busy` | Another Auto-Windscribe operation owns the lock. Stop or wait for it; do not kill the Windscribe helper |
| `connection_failed` / `tunnel_failed` | Request dispatch succeeded, but no tunnel was established |
| `connection_timeout` | Deadline elapsed; Windscribe may still be connecting. Review status; no automatic disconnect/retry follows |
| `state_conflict` | Connection location/state/protocol/port changed unexpectedly. Scan paused and uncertain metrics were discarded |
| `tunnel_unverified` | Route inspection could not establish a recognizable tunnel device. No probe traffic was sent |
| `https_failed` | Native state connected, but bound HTTPS did not succeed. Endpoint blocking, routing, or network policy may be involved |
| ICMP unavailable | Ping was blocked, missing or malformed. HTTPS still determines usability; RTT stays unknown |
| `command_timeout` | A CLI/probe child exceeded its deadline. Only that spawned child was terminated |
| `disconnect_timeout` | Official disconnect did not settle. No helper termination, firewall edit or service restart was attempted |

## Previous Stealth failure

The supplied `connect best stealth:443` attempt selected Stockholm / Fika but did not establish a
working tunnel. This is a historical user observation, not a result measured by Auto-Windscribe.
No RTT, speed or definitive censorship diagnosis can be inferred from it. Best-location selection
and exit code zero do not verify a tunnel. The new engine records the requested endpoint, observed
state and verification phase separately, so a future authorized test can distinguish dispatch,
establishment and HTTPS failures. It does not repeat the previous test automatically.

## Shared GUI state and cancellation

Do not change location/protocol or advanced GUI options during a scan. The CLI exposes city-level
identity in some responses, so same-city changes are not always detectable. If GUI preferences
change between sessions, use a different `settings_label` in the application profile to keep
observations separate. Available AmneziaWG presets do not reveal the selected preset.

Quit/Stop intentionally preserve the system VPN. A request already sent can finish after the TUI
exits. Inspect read-only status and choose an explicit Disconnect only if desired and authorized.
Resuming a scan requires a disconnected state and revalidates its remaining catalog entries.

## Terminal issues

Use an ordinary UTF-8 terminal, ideally at least 100×32. Tables scroll horizontally at narrower
widths. Tab/Shift+Tab move focus between controls. Escape returns focus to the server table.
Search uses fuzzy matching; short terms may also match similar nicknames. Use structured country,
city or nickname filters for exact selection. English UI is supported; gettext message boundaries
are in place for future Persian translation. Persian documentation is provided now.
