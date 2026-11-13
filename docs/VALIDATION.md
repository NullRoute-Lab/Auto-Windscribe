# Development validation record

Date: November 13, 2026. Scope: implementation and offline validation only.

| Check | Result |
| --- | --- |
| Python 3.12.12 | 115 tests passed |
| Host Python 3.14.10 | 115 tests passed |
| Coverage (statements and branches) | 84.48% |
| Ruff lint / formatting | Passed |
| mypy | Passed across 17 source modules |
| Package build | Wheel and source distribution built successfully |
| Archive audit | No reference trees, runtime databases, venvs or temporary check logs |
| Textual Pilot | Search, selection, profile editing, confirmation, scan cancellation and quit retention passed |
| Demo screenshot | Generated from synthetic adapters and temporary storage |
| Native read-only inspection | CLI help, version, status, locations, protocol ports and preset names |
| GitHub credentials | Authenticated account had repository write access |
| Reference cleanup | Only the two named extracted reference trees were removed after path verification |

All tests used fake subprocess/probe backends; the suite rejects real subprocess creation, socket
connections and DNS lookups. The synthetic demo has no native adapter and never persists into the
user's real history. The app was installed at the user level with no startup hooks. Only help,
version, read-only status/capability/catalog inspection and cache-only planning were used against
the installed executable.

No `windscribe-cli connect` or `disconnect` operation, VPN handshake, live ping, HTTPS reachability
probe, bandwidth test, firewall change, preference modification, system deployment change or
service restart was performed. The prior failed Stealth test was not repeated. Live operation
permission remains disabled, pending the separate explicit authorization checkpoint.

This record does not certify live tunnel performance. Native connected-state behavior, city-only
identity limits, tunnel interface recognition and probe reachability remain subject to a future
authorized real test. Git commit and remote verification are reported separately at handoff.
