# v0.3 recovery and desktop compatibility

## Established application defects versus unknown host causes

v0.2 `_scan` paused unconditionally for UNKNOWN. `_attempt` used UNKNOWN for dispatch,
status and verification errors as well as still-connecting timeouts. `_disconnected` checked
only native state/login, not baseline internet. `_disconnect` stopped waiting at the first
Disconnected response. Thus an ordinary completed failure could pause, while a failed
candidate with broken baseline connectivity could allow another attempt. None of that proves
why the user's internet actually failed. No real failure/recovery was reproduced during this
release. Stale routes, resolver restoration, firewall policy and helper state remain hypotheses.

v0.3 production composition adds NetworkHealthChecker and NetworkRecoveryManager to the same
serialized controller. Before each actual attempt it checks Google DNS, the requested two-packet
Google ping, Google HTTPS, independent configurable HTTPS (default direct-IP Cloudflare endpoint),
native state/firewall and default route presence. No interface override bypasses the firewall.
Raw resolver addresses, interface names, gateway addresses and response bodies are discarded.
Each subprocess is bounded; task-group cancellation cleans pending probes. Checks use normal
host policy, so DNS failure does not invalidate independently successful direct-IP HTTPS.

HEALTHY requires both HTTPS endpoints and DNS, plus ICMP. DEGRADED with only ICMP failure can
still be safe. DNS_FAILURE, HTTPS_UNAVAILABLE, NO_CONNECTIVITY, RECOVERY_PENDING and UNKNOWN
remain distinct. Other partial failures are DEGRADED but unsafe. NO_CONNECTIVITY means none
of these samples succeeded, not a claim about every Internet destination. Firewall On/Always On
is recorded but does not prove packet blocking. VPN_FIREWALL_BLOCKING is reserved for future
supported causal evidence and is never inferred just from the firewall label.

## State machine and limits

PRECHECK → CONNECTING → VERIFYING → failure → RECOVERY → BASELINE_HEALTHY → next candidate.
Recovery waits/observes up to two times by default with two-second settling intervals and a
25-second incident deadline, also bounded by the overall scan session budget. A scan-owned
matched connected tunnel may be gracefully disconnected through the existing official adapter.
Disconnected state does not trigger another disconnect. No new connect request is issued during
recovery. Ownership is relinquished after disconnect, so a subsequent connection is protected.

A completed failure or an observed Connecting → Disconnected transition can continue after
healthy baseline and a final Disconnected observation. An unacknowledged IPC timeout cannot be
made safe by merely waiting. An unexpected connected endpoint/protocol, Unknown native state,
late unowned tunnel or unsuccessful baseline restoration pauses the durable queue. Original
UNKNOWN results remain UNKNOWN in history; successful recovery is a separate persisted fact,
not a fabricated connection failure/success. Safely recovered candidates are completed and the
next pending candidate is selected. Known failures retry only under the saved retry preference.

Stop/Quit cancels application work without disconnecting. Pending native requests may finish
later. Native CLI has no request-generation ID or exclusive GUI lease; identical same-city /
protocol / port GUI actions cannot be distinguished. This release cannot prove universal absence
of a pending external request. It therefore pauses on unresolved dispatch and observable conflicts.
The user should leave the GUI idle during scans and inspect it before resuming an uncertain scan.

The automatic recovery switch controls post-failure recovery, not mandatory pre-attempt checks.
`--no-auto-recover` pauses on failure. `--max-recoveries` is the number of observation/health passes
(1–2), **not connection cycles**. Mandatory prechecks and stop-on-unsafe-baseline cannot be disabled.
The TUI P dialog exposes recovery toggle, count and delay. Status/progress messages are nonmodal.
`recovery-status` reads the last saved report; it does not initiate recovery.

## Why connection cycling is not shipped

The requested second tier may be enabled only if supported behavior preserves GUI preferences,
no pending operation exists, and an explicit validated recovery endpoint can be controlled safely.
The desktop CLI lacks preference snapshot/restore and an exclusive IPC lease. We cannot establish
those conditions. No Connect → Disconnect cycling option is offered or silently substituted.
The second recovery pass repeats bounded observations and health checks only. If this cannot
restore a baseline, stop and use the official application/support; no privileged reset is run.
This is a deliberate supported-interface limitation, not a claim that cycling is ineffective.

## Native “Switch to Auto” dialog

Reviewed the exact upstream
[MainWindow::onBackendProtocolStatusChanged](https://github.com/Windscribe/Desktop-App/blob/master/src/client/frontend/gui/mainwindow.cpp)
and [manual attempt strategy](https://github.com/Windscribe/Desktop-App/blob/master/src/client/engine/engine/connectionmanager/attemptstrategy/manualconnectionattemptstrategy.cpp).
The manual strategy emits a nonautomatic failure notification after its second internal failure.
The GUI's nonautomatic branch shows the dialog; accepting its action changes the shared automatic
preference and starts a fresh connection. Auto-Windscribe sends explicit protocol/port requests
through `connect -n`; that flag returns after IPC dispatch and does not isolate the GUI engine.
Installed 2.25.8 `help connect` confirms this dispatch behavior and offers no dialog suppression.

No popup visibility API was established. We warn about the possibility, not claim to detect a
visible window. The compatibility layer detects supported status identity mismatches only.
No Auto selection, synthetic clicks, accessibility automation, binary patching, helper termination,
firewall change or internal preference file edit is implemented. The program cannot guarantee
preservation of the official client's remembered connection arguments, nor restore unknown GUI
preferences. User-selected protocol arguments remain explicit.

An official headless architecture has no desktop MainWindow and therefore would avoid this GUI
code path, but is a separate installation and account/setup migration with different interfaces.
It is an optional future evaluation, not an in-place flag or a verified migration. No package or
service changes were performed. See the [official CLI/headless guide](https://windscribe.com/knowledge-base/articles/how-to-use-windscribe-on-linux-cli-and-headless).
