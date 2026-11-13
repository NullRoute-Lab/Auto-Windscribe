# Test authorization

Current development phase: **LIVE VPN TESTING IS NOT AUTHORIZED**.

The user's November 13, 2026 instruction authorizes implementation, read-only
CLI inspection, offline tests, repository cleanup, commit, and GitHub push.
It expressly prohibits real connect/disconnect operations, VPN handshakes,
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
