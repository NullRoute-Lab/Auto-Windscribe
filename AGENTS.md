# Auto-Windscribe development rules

## Mandatory authorization boundary

This development phase permits **offline, mocked, and dry-run tests only**.
Do not execute Windscribe connect/disconnect commands, network probes, ping,
speed tests, or live scans. Do not change firewall, DNS, routes, interfaces,
NetworkManager, Windscribe preferences/configuration, or system services.
Do not restart or terminate Windscribe or its helper. Never read or expose VPN
credentials, tokens, keys, or private configuration. Do not change bootc or
rpm-ostree deployments or install host packages.

Read-only CLI help/version/status/locations/ports inspection, documentation
browsing, project-local dependencies, offline checks, Git commit, and push to
https://github.com/NullRoute-Lab/Auto-Windscribe are authorized.

The application may implement live actions, but developers must not execute
them until the user explicitly authorizes a **separate live testing phase**.
Full filesystem/tool permissions are not VPN authorization. Do not bypass or
remove the application's safety gate for testing. Installation must not run a
scan. Never enable a background service. See docs/TEST_AUTHORIZATION.md.

## Implementation

Linux only; use the existing official /usr/bin/windscribe-cli. Preserve the
user's firewall and existing working connection. Quit must retain the tunnel.
Use argv subprocesses and injected fake adapters. Tests must prevent real
subprocess/network activity. Keep secrets, runtime SQLite files, caches, host
status, and local data out of Git. Prefer original code and minimal dependencies.

After review and original implementation, remove only the two explicitly
authorized reference directories AutoScribe-main and Windscribe-Analyzer-main,
after verifying their paths. Never commit those extracted projects.

Before handoff: run offline checks, review staged contents, commit, push without
force, verify the remote commit, then stop and request separate live-test
authorization. Do not assume that a resumed session has that authorization.
