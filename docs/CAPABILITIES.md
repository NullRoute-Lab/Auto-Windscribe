# Native CLI and advanced preferences

Scope: the installed Linux GUI package's `/usr/bin/windscribe-cli` 2.25.8. The CLI sends requests
to the official app over IPC; it does not create a second independent tunnel. Connection requests
may affect the official app's remembered last endpoint/protocol. Auto-Windscribe does not edit
its persistent preferences or promise to restore GUI defaults after live use.

The separately distributed **headless** Windscribe app documents an INI configuration surface.
That is not evidence that writing those values modifies this user's GUI package safely. We do not
read or write either configuration store, decrypt settings, or call undocumented reload commands.

Categories:

1. **Official CLI control:** a documented installed command safely represents the action.
2. **Read-only observation:** a known value/list is exposed but this controller will not change it.
3. **No verified safe control:** internal, GUI-only or version-specific configuration would be needed.

| Setting | Category and observed surface | Controller policy |
| --- | --- | --- |
| Endpoint / protocol / port | 1: `connect -n <nickname> <protocol>:<port>`; `ports <protocol>` | Validate against current capabilities and require explicit live consent |
| Active protocol / port | 2: `status` when connected | Show reported values; disconnected values are unknown |
| Auto vs manual protocol mode | 3: no installed preference getter/setter | App profiles explicitly choose each attempt; do not toggle GUI mode |
| Available AmneziaWG presets | 2: `amneziawg` lists names | `doctor` displays available names; selected preset remains unknown |
| Selected AmneziaWG preset / Protocol Tweaks | 3: CLI help directs selection to the GUI | Configure manually in official Anti-Censorship Settings |
| Circumvent Censorship | 3: no installed safe getter/setter | Unknown; no writes |
| Stealth / WStunnel | 1: connection protocol arguments | Supported only with dynamically discovered ports |
| Custom SNI | 3: no installed safe getter/setter | Unknown; no injection |
| Server routing: Auto / Regular / Alternate | 3: no installed safe getter/setter | Unknown; no routing preference writes |
| DNS policy / connected DNS | 3: no verified CLI getter/setter in this installed surface | Unknown; no DNS changes |
| Current firewall state | 2: `status` | Display Off/On/Always On when reported; preserve it |
| Firewall on/off command | 1 at CLI level, intentionally excluded from this app | Never called; firewall policy/mode stays under GUI control |
| Split tunneling | 3: no installed safe getter/setter | Unknown; no changes. Probe route must be a tunnel |
| IPv4 / IPv6 preferences | 3: no installed safe getter/setter | Unknown; probes use IPv4 only; no IPv6 assertions |
| MTU / packet sizing | 3: no installed safe getter/setter | Unknown; no interface writes |
| Keepalive | 3: no installed safe getter/setter | Unknown; no config injection |
| TLS padding | 3: internal extra-config mechanisms are version-specific | Unsupported; no extra config file writes |

The CLI's documented protocol list is WireGuard, UDP, TCP, Stealth and WStunnel. IKEv2 is excluded
from the supported Linux matrix. Port capabilities observed in the read-only review were:

| Protocol | CLI-reported ports in the development inspection |
| --- | --- |
| WireGuard | 443, 80, 53, 123, 1194, 65142 |
| UDP | 443, 80, 53, 123, 1194, 54783 |
| TCP | 443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 1194 |
| Stealth | 443, 587, 21, 22, 80, 123, 143, 3306, 8080, 54783, 8443 |
| WStunnel | 443 |

These are documentation of an observation, **not application defaults**. The app queries current
ports and refuses an unsupported choice because the native CLI can otherwise silently fall back.
No current server list or personal status output is bundled.

The unmarked tier of a location is unknown: upstream emits `(Pro)` for inaccessible premium entries,
not every premium entry. `(Disabled)` is also unavailable. This prevents falsely declaring the
entire Pro account's accessible catalog free. Region labels are retained; US and Canada region
names also receive a country label. Virtual region names are not converted into physical claims.

[Official CLI guide](https://windscribe.com/knowledge-base/articles/how-to-use-windscribe-on-linux-cli-and-headless),
[official headless configuration reference](https://github.com/Windscribe/Desktop-App/wiki/Linux-CLI-configuration-file-reference),
and [official CLI formatter](https://github.com/Windscribe/Desktop-App/blob/master/src/windscribe-cli/strings.cpp).
