# v0.5 — controlled desktop recovery and non-repeating jobs

GUI recovery is **optional and off by default**. This release implements the operation, but its
real-world effectiveness is unverified: development used fake processes and fake network adapters.
The reported Scan 6 screenshot establishes an observed timeout, later Disconnected/firewall On,
and failed baseline verification. It does not establish that restarting the GUI repairs internet.
With firewall On this implementation deliberately refuses restart, for the source-based reason below.

## What the official desktop does

The investigation used the **v2.25.8** source, matching the installed package, in addition to current
upstream. The following are source findings, not a new live experiment:

- [MainWindow manual failure handler](https://github.com/Windscribe/Desktop-App/blob/v2.25.8/src/client/frontend/gui/mainwindow.cpp)
  displays the Auto proposal for nonautomatic protocol failure. Accepting it changes protocol mode
  and reconnects. Native status does not expose whether that window is currently visible.
- [Linux SIGTERM handler](https://github.com/Windscribe/Desktop-App/blob/v2.25.8/src/client/frontend/gui/main.cpp)
  routes SIGTERM through the application's Qt shutdown machinery. It is not an isolated window hide.
  MainWindow shutdown invokes backend cleanup; Automatic firewall handling can change state on exit
  and subsequent startup. Always On has different behavior. CLI status exposes current firewall state,
  not enough policy detail to promise safe shutdown with firewall On.
- [Desktop CLI entry point](https://github.com/Windscribe/Desktop-App/blob/v2.25.8/src/windscribe-cli/main.cpp)
  can launch the desktop application when absent. Consequently calling `status` while the GUI is closed
  cannot establish independent headless control. The TUI suspends passive CLI status polls during
  restart and after an interrupted/failed restart. It does not inadvertently reopen a closed GUI.

There is no synthetic input, popup click, acceptance of Auto, preference injection, logout,
helper restart, SIGKILL fallback, firewall command, or routing/DNS modification. The GUI's own normal
shutdown writes its own persistent state; we do not claim that restarting an application has no
internal side effects. **Disable Auto-connect in the official GUI before enabling recovery.** No
supported CLI read proves that preference is disabled; confirmation communicates this prerequisite.
If a GUI startup nevertheless starts a connection, recovery pauses and preserves it.

A terminal-only desktop session is not offered: the desktop CLI auto-launch behavior contradicts
GUI-process independence. The [official CLI-only migration plan](HEADLESS_MIGRATION.md) remains the
recommended future evaluation for unattended scans. No packages/services were replaced in v0.5.

## Runtime verification and state machine

The installed RPM must report version 2.25.8. The manager reads its file list and the package's
root-owned desktop entry, resolves the absolute Exec path, rejects wrappers/extra arguments, and
cross-checks the executable against the official CLI installation directory. Root ownership and
nonwritable parent paths are required. No process command-line, environment or private config is read.

The process must match that exact executable, current UID and `/proc` start time. Multiple or
foreign-owned visible GUI instances are refused. A pidfd plus revalidation prevents PID-reuse signals.
The Linux glibc pidfd API is used through ctypes because some isolated Python builds omit those Python symbols. Only SIGTERM is sent to that exact GUI. Helpers and tunnel processes are never signal targets.
Unverifiable installations fail closed; an upgraded Windscribe version needs a new source review.

The controller holds its existing cross-process operation lock throughout:

`WAIT_FOR_NATIVE → GUI_RECOVERY_CHECK → GUI_CLOSING → GUI_STOPPED → GUI_STARTING →
CLI_REINITIALIZING → NETWORK_HEALTH_CHECK → BASELINE_VERIFIED → next unattempted candidate`

1. Ordinary v0.4 settlement/baseline recovery runs first. If it succeeds, no restart is necessary.
2. Automatic restart needs session opt-in and native failure evidence: `tunnel_failed`, a native
   Connecting→Disconnected failure, or a recorded native tunnel-failure diagnostic during settlement.
   A timeout alone is insufficient and is never described as detecting a popup.
3. Restart requires no unresolved dispatch, two separated authenticated Disconnected observations,
   firewall **Off**, and unchanged process identity. Connected (including scan-owned), Connecting,
   Disconnecting, Unknown, missing login, or On/unknown firewall causes refusal before signaling.
4. Wait for exact process exit, then launch the verified executable without a shell. Wait for a
   different process identity and responsive native status. No CLI command is issued while closed.
5. Require authenticated Disconnected/firewall Off again, a quiet interval, independent network
   health verification, and a final native check. Process launch alone never means recovery.
6. Only verified baseline recovery allows continuation. Failures retain the queue and pause.

Defaults: one restart per incident (within the requested maximum of two), two per confirmed scan
session; configurable session limit 1–4, graceful shutdown 15s, startup/CLI readiness 30s. The normal
recovery and scan-session deadlines bound the whole operation. One unsuccessful restart blocks
further automatic restarts in that session. A new manually confirmed session gets its own budget.
Cancellation sends no additional signal, launch, or disconnect; the GUI may remain closed. Open it
manually if needed. Never infer restored internet from Disconnected or from GUI RUNNING.

The process lock excludes other Auto-Windscribe controllers, not the official GUI or external tools.
Observed external state changes pause safely; no supported atomic ownership API prevents every race
with another application acting between observations. Leave other native controls idle during scans.
A pending crash reservation in another job blocks a new native connect/scan rather than hiding it.

## Durable attempts and deferred candidates

The SQLite result ID uniquely identifies an observation with scan ID, timestamp, context, complete
country/region/city/nickname payload, protocol, port and original outcome. GUI/native recovery rows
reference that result ID and candidate. Recovery does not recreate jobs, shuffle away checkpoints,
clear history, reset the current session budget, or automatically requeue an attempted combination.
In-flight reservations cover process/system crashes before outcome commit; ambiguous dispatches remain
unsafe and cannot be cleared by restarting a GUI.

A settled UNKNOWN is checkpointed with a separate **deferred** disposition. The UNKNOWN observation
is retained. Ordinary resume examines existing pending observations before issuing requests and skips
already processed candidates. Failure retry count remains zero by default; the existing explicit
`retries` profile option is bounded. Completed/confirmed failed candidates are not automatically retried.

`results --manifest ID` distinguishes attempted (distinct recorded candidates), verified, failed,
unknown, deferred, completed and remaining. Completed means processed/checkpointed, including deferred;
deferred is a subset, not an additional count to sum with completed. Remaining is the ordinary queue.
A reservation without a committed outcome remains remaining/uncertain, not falsely counted as a
completed handshake. The original selected matrix persists beyond the 1,000 displayed rows.

`retry-deferred ID` explicitly requeues only safely deferred UNKNOWN candidates in that same job.
It does not start a scan. Retry selection, removal of the disposition on reservation and outcome writes
are transactional. Old observations and linked recovery evidence remain. Native safety checks still
apply on subsequent `scan --resume ID`. No schema downgrade is supported: schema 4 migrates existing
v1/v2/v3 data additively; back up private application data before downgrading.

## Commands and interface

```bash
auto-windscribe                                      # TUI, single scan confirmation
auto-windscribe scan --profile exhaustive --gui-restart-recovery
auto-windscribe scan --resume 6 --gui-restart-recovery # session opt-in; original job unchanged
auto-windscribe recover-gui                          # manual popup/blockage report + confirmation
auto-windscribe recover-gui --dry-run                 # no process/native/network operation
auto-windscribe results --manifest 6 --offline --json
auto-windscribe recovery-status --offline --json
auto-windscribe retry-deferred 6                      # explicit requeue; no VPN action
auto-windscribe scan --resume 6
```

Use `--live` for explicit scripting consent. `--offline` and `--dry-run` never restart, probe or
change persistent records. New jobs accept `--gui-restart-limit`, `--gui-stop-timeout` and
`--gui-start-timeout`; resumes retain saved limits and only override GUI restart opt-in for the session.
TUI P exposes the same preferences. G / **Recover GUI** is manual recovery; Ctrl+R / **Retry deferred**
requeues the latest job with deferred candidates. Continue scan keeps the original job. Resume uses
the current TUI GUI-recovery toggle for this session while preserving the saved job's scope/limits.
GUI/CLI/native/firewall/internet/restart-count indicators report observations from recovery; outside
recovery, uninspected GUI state is explicitly unknown. No popup-presence indicator is fabricated.

## Development authorization

No actual GUI termination/relaunch, VPN operation, DNS lookup, ping or HTTPS health probe was performed
to validate this release. Installed CLI help, RPM file metadata and executable/process identity may
be inspected read-only. Fake process and network adapters validate state transitions, budgets,
serialization, PID/executable/UID checks, failure/cancellation, durable deferral and safe continuation.
Fresh permission is required after publication for a bounded real-world GUI recovery test.
