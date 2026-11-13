# Security and operating boundaries

Auto-Windscribe controls an existing authenticated Windscribe application. Its permissions are
narrower than the shell account's permissions. It never collects credentials, reads private keys,
opens Windscribe configuration or logs, sends debug logs, or invokes login/logout/update/firewall.
It never changes DNS, routing, firewall rules, NetworkManager, rpm-ostree, bootc or system services.
Live connection requests themselves naturally cause the official client to manage its tunnel and
apply the user's existing network/firewall policy.

## Consent and isolation

- Default config disables live operations and automatic scanning.
- Live actions require one interactive session confirmation or explicit `--live` for scripts.
  There is no persistent consent prerequisite. Offline/dry-run overrides all consent.
- `AUTO_WINDSCRIBE_OFFLINE=1` prevents all live controller/probe actions even if consent is enabled.
- `--offline` / `--dry-run` compose an adapter that cannot invoke Windscribe.
- `--demo` uses separate fake adapters and temporary storage, never the user's history.
- Tests prohibit native subprocess creation and socket connections, then explicitly inject mocks.
- An operation lock serializes mutating actions across application processes; no parallel tunnels.
- Shell interpolation is never used. Nicknames and protocol/port values are passed as argv elements.
- Child command output is bounded, command deadlines are enforced, and raw stderr is not logged.

The live gate prevents accidents, not a hostile user who can edit Python source or their own files.
Do not treat end-user application consent as permission for an agent to ignore docs/TEST_AUTHORIZATION.md.

## Data minimization

App-owned XDG directories use mode 0700, JSON and SQLite files 0600. JSON replacements are atomic;
SQLite records observations and queue completion together. Profiles reject unknown keys, including
credential fields. Context uses short manual labels and test settings, not SSIDs, ISP lookup,
public IPs or network-interface MAC addresses. Native status is parsed into an allowlisted model.
IP and usage fields are discarded. Logs are curated diagnostics rather than command transcripts.

History reveals requested VPN endpoints, timestamps, quality and user-defined context. Keep it
private. Git excludes runtime databases, caches, reports, secrets and environment files. Review
any diagnostic data before manually sharing it. There is no automatic telemetry or issue upload.

## Limits

The shared native CLI has no connection lease; GUI changes to the same city/protocol/port may be
indistinguishable. Leave GUI controls idle during scans. Stop, cancellation and quit preserve the
current system VPN state, including an in-flight request that may later establish a tunnel. A hung
state is reported, never repaired by killing the helper or changing the firewall.

Probe traffic is bound to a verified tunnel device. This is not proof against every routing race,
split-tunneling policy, device reuse or another privileged program's actions, and it is not a leak
certification. Default HTTPS uses a literal IPv4 address; the optional download uses normal OS DNS.
The optional test contacts Cloudflare and transmits up to 1 MB. It is off by default.

## Reporting

Report non-sensitive defects through the repository's GitHub Issues. For a vulnerability, use
GitHub's private vulnerability reporting if enabled, or contact repository maintainers privately.
Do not attach account tokens, keys, full Windscribe logs, or personal configuration to public issues.

Transport preflight only accepts an injected trusted inventory. The shipped native inventory
cannot establish protocol-to-address provenance and performs no probes. TCP evidence never counts
as verified VPN connectivity. No arbitrary host/port scan input is exposed. Offline reads snapshot
only the app database/WAL into temporary storage so even WAL read marks remain untouched.
