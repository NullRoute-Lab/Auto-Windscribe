# Contributing

Read AGENTS.md and docs/TEST_AUTHORIZATION.md first. Ordinary development and CI are offline only.
Do not change the user's VPN state to reproduce a bug. Fake adapters are the default test surface.

```bash
uv sync --frozen
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
AUTO_WINDSCRIBE_OFFLINE=1 uv run pytest
```

Use the existing native Linux CLI, preserve the firewall, keep terminal workers cancellable and
never disconnect in shutdown cleanup. Add regression tests for meaningful safety or state-machine
changes using tests/fakes.py. Test subprocess cleanup through a fake process factory, not a genuine
VPN command. Do not remove the test tripwires to make a failing test pass.

Keep dependencies small and user-installable on an immutable Fedora host. Avoid implementation for
other operating systems, GUI frameworks, background daemons, credentials or internal Windscribe
configuration readers/writers. Persist missing observations as null. Changes to success criteria or
ranking must update the explanation and tests. New CLI output formats should get sanitized,
synthetic fixtures, not copies of a user's full status or logs.

Explain the behavioral change and offline validation in a pull request. Do not include runtime
history, profiles with personal labels, private logs, tokens or account details. Any future real
VPN test requires explicit authorization separate from a build, commit or push request.
