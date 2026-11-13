# Installation, upgrade and removal on Bluefin

Use the native, already authenticated Windscribe 2.25.8 package and helper. Do not reinstall it.
No host RPM package, root privilege, system deployment change or new service is needed.

## Isolated uv tool

```bash
uv tool install --python 3.12 git+https://github.com/NullRoute-Lab/Auto-Windscribe.git
~/.local/bin/auto-windscribe --help
~/.local/bin/auto-windscribe --demo
```

If Python 3.12 is already installed, uv can use it. A newer Python 3.12+ is also supported; see CI
for tested versions. Installation does not import the application entry point or execute any VPN
command. There are no startup hooks, autostart entries or background daemons.

For reproducible development from the checkout:

```bash
uv sync --frozen --python 3.12
.venv/bin/auto-windscribe --demo
```

The lockfile pins the development dependency graph. The installed application uses the supported
Textual version range from pyproject.toml. The console-script path is normally `~/.local/bin`.

## Standard Python virtual environment alternative

Use an existing Python 3.12+ interpreter:

```bash
python3 -m venv ~/.local/share/auto-windscribe/venv
~/.local/share/auto-windscribe/venv/bin/python -m pip install git+https://github.com/NullRoute-Lab/Auto-Windscribe.git
mkdir -p ~/.local/bin
ln -s ~/.local/share/auto-windscribe/venv/bin/auto-windscribe ~/.local/bin/auto-windscribe
```

Do not replace an existing command/symlink without checking it. Do not use `sudo pip` or install
host packages. Add `~/.local/bin` to your PATH using your normal shell configuration if needed.

## Native optional probe tools

Live measurement uses existing `/usr/sbin/ip`, `/usr/bin/ping` and `/usr/bin/curl`. Read-only
browsing does not require probe tools. If unavailable, diagnostics report unknown/unavailable
metrics; the app will not install host packages. curl must support `--interface if!DEVICE`.

## Storage and permission

Configuration, data and cache follow XDG defaults. No config file is required: defaults are
Turbo and a launch scan offer with one interactive confirmation. `--live` supplies explicit
consent for scripts; there is no persistent permission switch. Old `live_enabled` fields are
ignored. `auto_scan=false` suppresses the launch offer; the TUI's A key toggles this preference.
Installation never launches a scan. Development-agent test authorization is documented separately
in TEST_AUTHORIZATION.md and is not a product lock.

## Upgrade

```bash
uv tool upgrade auto-windscribe
```

If this development session installed a local wheel, switch that installation to the Git source
for future remote updates:

```bash
uv tool install --reinstall --python 3.12 git+https://github.com/NullRoute-Lab/Auto-Windscribe.git
```

For a venv installation, repeat `python -m pip install --upgrade` using its interpreter and the
same Git URL. To pin a Git revision, append `@COMMIT_SHA` to the URL. Quit Auto-Windscribe before
upgrading; an existing Windscribe tunnel remains connected. Back up the application data directory
if needed. Unknown newer database schema versions are refused rather than silently overwritten.

## Uninstall

```bash
uv tool uninstall auto-windscribe
```

For a manually created venv, remove only that venv and its own symlink. Application history and
profiles are retained. Optionally delete the three `auto-windscribe` XDG directories after checking
their paths. Do not remove the user's Windscribe directories, account or helper service.
