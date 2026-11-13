# Test authorization

Current phase: **ONE LIVE TEST AUTHORIZATION CONSUMED — NO FURTHER LIVE TESTS AUTHORIZED**.

After verified publication of commit `48faae1f8064d759ccc1ac7ac894e8687109af95`,
the user explicitly answered "yes" to the checkpoint question. That authorized
one bounded connection attempt and conditional tunnel-bound HTTPS verification.
The attempt has been performed; it must not be repeated under that permission.

The test used a temporary application consent scope, a disconnected starting
state, no retries, no ping, no download/upload measurements, and no automatic
scan. The temporary consent was removed. Normal application live permission and
automatic scanning remain disabled. No explicit disconnect, GUI preference edit,
firewall command, or service restart was performed. Runtime observations remain
in private XDG application storage, outside Git.

Another connection attempt, full scan or network probe requires new explicit
authorization. Read-only inspection and offline development remain permitted.

The previous development-only boundary below remains the historical record.

The user's November 13, 2026 instruction authorizes implementation, read-only
CLI inspection, offline tests, repository cleanup, commit, and GitHub push.
That original development phase expressly prohibited real connect/disconnect
operations, VPN handshakes,
connectivity probes, ping, bandwidth tests, firewall or network changes,
Windscribe preference edits, helper/service restarts, credential inspection,
and automatic live scans. The existing authenticated environment must remain
intact. Documentation browsing and GitHub publication are allowed network use.

The known previous `connect best stealth:443` failure is supplied context, not
permission to repeat it. An exit code of zero is not proof of a working tunnel.

Offline tests must use fake subprocesses and probe adapters. Do not run the
default application against a live backend during development. User-level
installation is allowed only if it has no startup hooks or VPN side effects.

After implementation, offline validation, cleanup, commit, and verified push,
stop and ask exactly:

"The implementation has been committed and pushed to GitHub. Do you authorize
me to run the first real Windscribe connection test now? It may temporarily
interrupt your internet connection. (Yes/No)"

If pushing fails, report the blocker and do not claim this checkpoint is
complete. No answer, elapsed time, tool permissions, or permission to build
constitutes live-test authorization. A future explicit Yes must be scoped and
recorded before any live test. This file is an instruction to agents, not a
runtime permission token.
