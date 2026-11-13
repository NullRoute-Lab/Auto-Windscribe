> v0.5 adds opt-in GUI recovery and durable deferred/retry dispositions; see [GUI_RECOVERY.md](GUI_RECOVERY.md). Existing settlement rules below remain applicable.

> Current v0.4 behavior: see [V04.md](V04.md). Earlier release descriptions below are historical.

# Troubleshooting

Start with `auto-windscribe doctor`, which reads native version/status/capabilities and lists
available AmneziaWG preset names. It performs no ping, HTTPS, speed or VPN connection test.
Use `doctor --offline` to avoid even native CLI calls.

| Diagnostic | Meaning / next step |
| --- | --- |
| `authorization_required` | Confirm the interactive session, or use --live for scripting; no config switch is needed |
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

## v0.2 phase diagnostics

- `dispatch_timeout` / `ipc_timeout`: request dispatch or serialized CLI work exceeded its budget.
  The request may have reached the official app; inspect native status before another action.
- `status_timeout`: a bounded status poll failed to finish. This is not evidence of a VPN handshake failure.
- `connection_timeout`: no connected state observed before the establishment deadline. See saved
  state labels. Connecting means uncertain and paused; positively disconnected can continue.
- `validation_timeout` / `tunnel_unverified`: route/device validation failed; no probe was sent.
- `https_timeout` / `https_failed`: connected-state verification failed, not necessarily tunnel negotiation.
- `endpoint_unavailable`: expected on CLI 2.25.8; trusted protocol-address metadata is unavailable.
  Turbo skips TCP preflight and uses native verification; there is nothing to unlock or install.
- `tcp_only`: temporary endpoint transport evidence, never a verified VPN result.

A single WStunnel:443 timeout cannot establish censorship. Do not disable the firewall or restart
helpers as automatic recovery. Compare phase records, use read-only doctor/status, and change
GUI-only preferences manually if desired. Change the application settings label after such a change.
See [the failed-test analysis](TURBO.md). Stop/Quit may leave a native request still finishing.

## v0.3 network recovery

Baseline DNS, Google ping, Google HTTPS and independent HTTPS are checked before each native
attempt. ICMP-only failure does not stop a usable baseline. Recovery observes native state,
gracefully disconnects only a scan-owned tunnel when needed, waits and checks baseline again.
Verified recovery continues the saved queue; unresolved IPC or GUI conflicts pause safely.
No firewall disabling, host networking edits or speculative connection cycles are supported.
Native Switch to Auto prompts cannot be suppressed. See [recovery analysis](RECOVERY.md)
(from the repository root; in docs use RECOVERY.md).

```bash
auto-windscribe
auto-windscribe doctor
auto-windscribe network-check --dry-run
auto-windscribe network-check                 # asks before real health probes
auto-windscribe scan --profile turbo --auto-recover --max-recoveries 2
auto-windscribe recovery-status
auto-windscribe results --history
```

Scripts need `--live` for active probes/scans. `doctor` and `recovery-status` are read-only.
Max recoveries counts health/settling passes, not extra VPN connections. Mandatory baseline
checks cannot be disabled. Stop/Quit retains VPN state. Real recovery efficacy is not yet verified.
