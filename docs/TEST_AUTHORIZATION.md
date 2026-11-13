# Development test authorization

**Current v0.2 session: offline implementation and validation only.**

The prior single real connection test was authorized after the v0.1 publication;
that permission was consumed. It does not authorize Turbo preflight, another
connection, HTTPS checks, ping, or a live scanning session.

The user now authorizes implementation of the normal unlocked end-user experience:
TUI confirmation once per bounded scan; interactive CLI confirmation or explicit
`--live` for scripting; no persistent live-enabled configuration switch. This
product permission model does not authorize the developing agent to accept those
prompts. No hidden permanent development lock should remain in the application.

Allowed now: inspect application history and read-only native CLI capabilities;
research official docs/source; write code; install isolated dependencies; execute
fake-adapter tests and synthetic benchmarks; update the user-level executable;
commit and push to the designated repository; check offline GitHub Actions.

Not allowed now: actual endpoint DNS/TCP/TLS probes, VPN requests, reachability or
bandwidth checks, privileged namespaces, additional tunnel engines, system network
changes, helper restarts, credential inspection or GUI preference modifications.
The application may implement supported live operations without running them.

After implementation, offline tests, packaging, installation, verified push and
passing GitHub Actions, report results and ask for new explicit permission for a
small bounded real-world Turbo Discovery test. The app must meanwhile be ready
for the user to run directly and confirm a scan. Never interpret earlier consent,
tool permissions, or publication permission as new testing authorization.
