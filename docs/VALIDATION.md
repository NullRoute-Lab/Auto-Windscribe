# v0.2 offline validation

November 13, 2026. This upgrade used injected fake VPN, subprocess, DNS and TCP adapters.
No real preflight, VPN connection/disconnection, ping or HTTPS reachability test was run during
this development session. The earlier single authorized WStunnel test belongs to v0.1 and its
permission is consumed; [TURBO.md](TURBO.md) explains what its timeout does and does not prove.

| Check | Result |
| --- | --- |
| Python 3.12.12 | 142 offline tests passed |
| Python 3.14.10 | 142 offline tests passed |
| Ruff lint / format | Passed for source, tests, benchmarks and packaging scripts |
| mypy | Passed across 18 application modules |
| Package build | Wheel and source archive built; archive contents audited |
| Textual Pilot | One-Enter startup confirmation, no LIVE LOCKED header, search/filter/profile editing, cancellation, existing-connection protection and quit retention |
| Runtime data safety | Offline/dry-run no persistent writes; database/WAL bytes unchanged by offline snapshot reads |
| Preflight | Bounds, rate/retry/probe budgets, DNS caching, socket cleanup, partial failures, unknown UDP, stale/private metadata refusal |
| Native controller | Serialized commands, separate phase deadlines, slow-poll handling, conflicts, sequential attempts, resume and retained tunnel |
| Mock benchmark | 3.206851 s sequential / 0.414847 s bounded concurrent; 7.730×, no network operations |

Coverage: 85% rounded, including branches. Raw coverage artifacts remain local and ignored.
Benchmark raw synthetic measurements are published in benchmark-v0.2.json; timing thresholds are
not CI assertions. Official CLI help/catalog and public upstream sources were inspected read-only.
The app's prior failed-test record was inspected without publishing private runtime data.

The user-level executable is updated using the built wheel. Verification uses version/help,
strictly offline commands, and synthetic Textual tests; installation has no runtime hooks.
GitHub's offline matrix runs on Python 3.12 and 3.14 after push. The exact final commit and remote
CI outcome are reported at handoff, since a commit cannot embed its own hash.

Known limits: no trustworthy native endpoint export for TCP preflight; no successful live tunnel
certified; native status can expose only city-level identity; GUI-only advanced preferences remain
unobservable/unchanged; IPv6/DNS-leak certification and parallel proxy/namespace tunnels are outside
this release. No host packages, routes, DNS, firewall, GUI preferences or helper services changed.

## v0.3 update

Offline regression suite expanded to 170 tests, including baseline health classification,
Google command argv, output redaction, cancellation, owned-tunnel disconnect, two failed recovery
passes, delayed unowned connections, GUI identity mismatch and automatic safe continuation.
All 142 v0.2 tests remain present and passing. No real health probe or VPN operation was run.
Read-only native `help connect` and upstream source were inspected. Real internet-loss root cause
and recovery effectiveness remain unverified. See RECOVERY.md for established defects and limits.

## v0.4 validation

178 offline tests pass on Python 3.12 and 3.14, including late settlement beyond the old two-check
window, firewall transitions, unknown outcomes with independent settlement evidence, checkpoint
persistence across restart, hard-crash reservations, cancellation during recovery, and execution
past the thousandth queued candidate. Source/branch coverage is approximately 85%. Ruff, mypy,
wheel/source archive audits and the GitHub offline matrix are required before handoff. No new live
scan, health probe, GUI automation or system change is part of this release's validation.

The installed executable is verified with version/help/dry-run and fake-adapter TUI checks.
Public source research and private read-only application history inspection informed V04.md.
No private runtime database, configuration or native raw log is included in the commit.
