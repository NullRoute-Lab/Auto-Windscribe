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
