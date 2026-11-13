# Turbo Discovery: evidence, limits, and failure analysis

## What the failed first test proves

The authorized v0.1 test requested Amsterdam / Stroopwafel, WStunnel:443, with a 45-second
application deadline. It recorded `connection_timeout`, no successful tunnel/HTTPS verification,
and final native state Disconnected. That proves only that the application did not observe a
verified connection in the allotted time. It does not establish universal blocking, censorship,
authentication failure, a broken server, DNS failure, TLS failure, or a routing/firewall conflict.
The record has no phase timings or status transition sequence; retrospective certainty is impossible.

In v0.1 the establishment timeout also enclosed capability/catalog/status reads before dispatch.
Native commands were serialized, but waiting for their lock was outside each command's deadline.
A two-second disconnected grace could report failure before an asynchronously dispatched request
was acknowledged. These were application-level confounders, independent of network conditions.

In v0.2:

- CLI capability/catalog preparation is separate, cached for at most five minutes, and revalidated
  against selected unique nickname/port. Native reads remain serialized. Queue wait plus command
  execution have a combined ten-second bound. `connect -n` sends one IPC request, not a tunnel.
- Dispatch has its own profile deadline. Establishment starts after dispatch returns. Observed
  establishment time excludes discovery and IPC time, which is recorded separately. The CLI has no
  native handshake timestamp; the displayed time is an observation bounded by polling cadence.
- Each status poll gets a separate deadline. A poll already in progress may finish after the
  establishment deadline; a connected result is evaluated, not discarded as a timeout. Maximum
  establishment observation overrun is one status-poll budget, subject to the session deadline.
- An explicit native diagnostic or Connecting → Disconnected transition permits early failure.
  Remaining Disconnected without an acknowledgment is observed until the deadline, not guessed
  failed after two seconds. Connecting at deadline is uncertain and pauses; Disconnected at deadline
  permits the next candidate. There is no automatic helper recovery or blind disconnect.
- Route validation and HTTPS have separate deadlines (defaults 5 and 12 seconds). Quality probing
  remains cancellable, bounded and interface-bound. State changes discard measurements.
- A result contains phase, measured phase durations, and up to 32 distinct state/diagnostic labels.
  No raw CLI output, addresses, account data or private logs are persisted. Known native diagnostic
  labels remain distinct; absent evidence stays unknown. A TCP preflight failure is transport
  evidence for that endpoint at that instant, not the cause of a later VPN failure.

## Inventory trust and native limitation

Stage A uses `locations`, `help connect`, and `ports <protocol>` from the installed official CLI.
No static matrix or server list replaces that catalog. CLI premium markers identify inaccessible
entries, not all Pro servers; undisclosed tier remains unknown. Full filtered inventories survive
budget limits; exact nickname ambiguity is rejected.

Official source review, November 13, 2026:

- The [native attempt strategy](https://github.com/Windscribe/Desktop-App/blob/5af57dac8d74fe202e53ccc21af0eb494c67df61/src/client/engine/engine/connectionmanager/attemptstrategy/iconnectionattemptstrategy.cpp)
  selects the node address using the port map's IP index.
- The [PortMap parser](https://github.com/Windscribe/Desktop-App/blob/5af57dac8d74fe202e53ccc21af0eb494c67df61/src/client/client-common/api_responses/portmap.cpp)
  maps protocol `use` fields to `ip`, `ip2`, or `ip3`.
- The [official wsnet request factory](https://github.com/Windscribe/wsnet/blob/aa297ca6a6d3238c236d42c165f7668ca897795d/src/api/serverapi/serverapi_requestsfactory.cpp)
  provides a legacy public server-list path, but PortMap and newer inventory requests attach account
  authentication. The public list alone does not establish the effective protocol address mapping.
- [Official server structures](https://github.com/Windscribe/wsnet/blob/aa297ca6a6d3238c236d42c165f7668ca897795d/include/wsnet/WSNetServerLocations.h)
  distinguish nicknames, nodes, ping hosts and multiple addresses. These are not interchangeable.

These source revisions inform the trust design, not a claim that their internal API is a supported
CLI 2.25.8 interface. We do not extract auth hashes, decrypt cached settings, call authenticated
APIs, import third-party IP lists, or infer endpoint names. `NativeInventory` deliberately returns
no endpoints. Stage B reports inconclusive and makes zero real transport requests in this release
with that adapter. This limitation prevents promising a real preflight speedup on the target host.

The implemented `Inventory` interface is the extension boundary for a future official safe export.
An adapter must establish source, freshness, exact datacenter identity, protocol address mapping,
current CLI port compatibility and account availability. It must not accept arbitrary target text.
`TCPTransport` rechecks expiry/public addresses and pins numeric DNS results to prevent local-network
rebinding. Resolved addresses are held in memory, never stored with observations. DNS has a
single-flight cache, TTL and size cap; libc resolver work already running in the executor may finish
following cancellation, but no TCP operation continues. TLS is not attempted without verified
protocol framing and SNI. UDP/WireGuard are never marked reachable based on silence.

## Scheduling and checkpoints

Stage B deduplicates address/port checks and bounds worker count, per-check time, batch time,
start rate and total attempted probes (retries included). One batch deadline includes inventory
loading. Cancellation awaits all async workers and closes sockets. Partial results are saved as
separate preflight observations; they never populate RTT, packet loss or VPN success statistics.

Stage C recomputes its next choice after each observation. Bonuses favor unseen protocols (30),
countries (20), datacenters (12) and protocol/port pairs (8). A verified historical score contributes
up to 35, fresh TCP success adds 8, temporary TCP failure subtracts 8, and a native failure inside
cooldown subtracts 35. Every third pick favors unexplored combinations (+40) without the success
bonus. These selection weights are **not** the quality leaderboard formula. Failures are delayed,
not permanently excluded. Every native attempt remains serial under the process-wide operation lock.

Turbo stops on verified success; previously measured fast success can shorten its establishment
ceiling to `min(configured, max(3, 2*previous_seconds + 3))`. Balanced measures more candidates.
Exhaustive preserves every supported selected combination and processes it in bounded sessions.
All modes checkpoint completed candidates transactionally. Unknown/cancelled candidates remain
pending. Resume gets a fresh explicit session budget and uses the saved profile; completed rows
are not repeated. A hard process crash can leave an uncommitted attempt pending, never fabricate
completion. Session timeout cancels work and retains the native state. It does not force tunnel cleanup.

Schema v2 adds a separate preflight table and migrates v1 non-destructively. Old observations remain
readable; v2 context hashes separate measurement semantics from v1. Profile strategy/time budgets
are not part of the v2 context, allowing Turbo and Balanced to share reliability observations for
matching network/settings/measurement labels. Context is manual and non-sensitive, with no IP/SSID lookup.

## Proxy and namespace research

Windscribe's [Proxy Gateway](https://windscribe.com/features/proxy-gateway) serves HTTP/SOCKS to
other devices through the **existing desktop VPN connection**. It does not provide independently
authenticated per-datacenter sessions, so probing several local proxies would not verify several
VPN endpoints. Windscribe's [SOCKS5 comparison](https://windscribe.com/blog/vpn-vs-socks5/) states
that its separate SOCKS5 server offering was discontinued in April 2022. No current official
independent endpoint API was established in this research; no proxy-testing implementation is shipped.
If such a supported API appears, a future adapter would need separate explicit credential handling,
endpoint provenance, quotas, and proxy-specific results; those still would not prove native VPN success.

Linux [network namespaces](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) isolate
network devices, stacks and routes. [unshare(2)](https://man7.org/linux/man-pages/man2/unshare.2.html)
describes the capabilities/user-namespace prerequisites. Independent usable tunnels would also need
network plumbing, credentials and additional tunnel instances; the official shared desktop helper
has no supported independent-session interface here. User namespaces alone do not create independent
outbound VPN sessions. Future namespace experiments would require a separately approved privileged
architecture and explicit isolation/cleanup design. This release creates no namespaces, interfaces,
routes, firewall rules, or additional VPN engines and remains unprivileged on immutable Bluefin.

## Synthetic performance experiment

Run `uv run python -m benchmarks.preflight`. The script blocks real process creation, DNS and socket
connects independently of pytest, and injects 32 synthetic endpoints with a 100 ms async delay.
It compares the old sequential scheduling strategy (concurrency 1) with concurrency capped at 16,
using the same 100 starts/s rate cap, budget 32, no retries, three repetitions each.

Python 3.12.12 on Linux x86_64: medians **3.206851 s** and **0.414847 s**, ratio **7.730×**.
Observed peak concurrency was 1 and 10 respectively; the start-rate cap explains why it stayed below
16. [Raw timings](benchmark-v0.2.json) include all repetitions. Measurements validate concurrent
scheduling only. They neither measure native VPN performance nor predict time-to-success, and do
not remove the native inventory limitation. CI checks behavior, not timing-sensitive speedup targets.
