# Auto-Windscribe agent instructions

## Current development authorization

The v0.5 Turbo Discovery implementation is authorized. The earlier permission
covered one live connection test and is consumed. During this development session,
**do not execute VPN connect/disconnect, DNS endpoint resolution, TCP/TLS endpoint
probes, ping, HTTPS connectivity checks, benchmarks over real networks, or scans**.
All implementation tests and performance benchmarks must use injected fake
adapters. Do not start the normal app in a way that accepts a live scan prompt.

Read-only native help/version/status/locations/ports, private application history
inspection, official documentation/source research, local dependencies, offline
checks, user-level installation, Git commits and pushes to
https://github.com/NullRoute-Lab/Auto-Windscribe are authorized. Metadata source
research is not permission to probe the advertised endpoints.

## End-user behavior is different from agent authorization

Implement deliberate live actions without a persistent `live_enabled` lock.
A TUI session confirmation or explicit CLI --live authorizes a bounded operation
for the end user. Never require editing JSON to use live features. --offline and
--dry-run must not change network state or persistent application data. The
optional offline environment switch is a user/testing control, not a hidden
mandatory development lock. Normal startup must offer a single Turbo confirmation
when disconnected; never auto-accept it during development.

## Invariants

Linux/Bluefin only; retain the existing official /usr/bin/windscribe-cli and
shared system tunnel. One controller operation and one native connection attempt
at a time. Parallel preflight may only target addresses evidenced by trusted
official inventory, with budgets and cancellation. Never equate TCP reachability
with VPN success or label TCP timing as VPN ping. Unknown UDP evidence is unknown.

Preserve existing working connections and firewall/GUI preferences. No helper
termination, credentials, private Windscribe config/log inspection, namespace
creation, extra VPN engines, system package layering, routes, DNS, or host firewall
changes. Quit/Stop retain the tunnel. Uncertain states pause for diagnosis.

Keep runtime observations, endpoint inventories and local benchmark artifacts out
of Git. Public benchmark documentation may contain only measured synthetic test
results with methodology. Complete offline lint/types/tests/build and GitHub
Actions, update the local executable, commit/push, verify remote, then stop and
ask for new permission for a small bounded Turbo test. See docs/TEST_AUTHORIZATION.md.

This session forbids real Google/DNS/HTTPS health checks and recovery cycles. Use fake adapters only.

All prior v0.3/v0.4 live-test authorizations are consumed. v0.5 development is offline only, including settlement, firewall and exhaustive tests. No live probes or native mutations before fresh permission after publication.

GUI recovery implementation is authorized; real GUI shutdown/relaunch is NOT. Use fake GUI processes. Prior live permissions are consumed. No helper signals or host networking changes.
