# Offline testing

The first release is validated with simulated adapters, not real VPN traffic. The ongoing agent
boundary is in [TEST_AUTHORIZATION.md](TEST_AUTHORIZATION.md) and the root AGENTS.md.

```bash
uv sync --frozen
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
AUTO_WINDSCRIBE_OFFLINE=1 uv run pytest --cov=auto_windscribe --cov-report=term-missing
uv build
```

`tests/conftest.py` globally blocks real subprocess creation, socket connects and DNS resolution.
Tests of the subprocess lifecycle replace asyncio's process factory with a fake process. Tests of
the native adapter use an exact argv/response script. No shell command can silently become a real
VPN integration test. Fixtures use invented names and documentation-only IP addresses, not the
host's public IP, credentials, private configuration or complete live catalog.

Coverage includes status states/errors/warnings, capability and catalog parsing, malformed output,
argument safety, connection success/failure, timeouts, cancellation, subprocess cleanup, explicit
replacement, concurrent lock exclusion, GUI interference, rank scans, retries, durable resume,
profile validation, cache fallback, contextual rankings, fuzzy search, ping loss/jitter, probe
binding, consent enforcement and signal callback restoration. Textual Pilot runs the actual app
with synthetic adapters to exercise search, dialogs, confirmations, responsiveness and quit retention.

Synthetic demo:

```bash
auto-windscribe --demo
auto-windscribe scan --demo --dry-run --json
```

Demo data is clearly labeled and temporary. It is never mixed with real observations and is never
a fallback for native discovery. Ordinary commands with `--dry-run` require the user's existing
cached catalog and cannot refresh it.

No live test is runnable through the normal suite. The pytest `live` marker is reserved for a
future separately authorized suite, which must not be included in offline CI. A future live phase
must first obtain explicit permission, define the single operation to test and report its effects.
Build or push permission is never live-test permission.
