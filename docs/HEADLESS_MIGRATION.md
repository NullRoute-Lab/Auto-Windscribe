# Optional official headless migration — plan only, separate approval required

Recommendation: evaluate the official Linux CLI-only package for long-running unattended scans.
It removes the desktop MainWindow architecture responsible for dialogs. This is stronger evidence
than hiding a window, but does not certify handshake success, Bluefin package compatibility or
elimination of native networking bugs. Auto-Windscribe remains on the installed desktop package.

Evidence:

- [Official CLI/headless guide](https://windscribe.com/knowledge-base/articles/how-to-use-windscribe-on-linux-cli-and-headless)
  distinguishes desktop and headless builds and documents Fedora CLI packages, normal-user commands,
  login, and a separate documented configuration surface. CLI-only is a separate installation.
- [Native GUI handler](https://github.com/Windscribe/Desktop-App/blob/master/src/client/frontend/gui/mainwindow.cpp)
  handles manual failure with an Auto proposal; accepting changes shared settings and reconnects.
- [Manual strategy](https://github.com/Windscribe/Desktop-App/blob/master/src/client/engine/engine/connectionmanager/attemptstrategy/manualconnectionattemptstrategy.cpp)
  emits the nonautomatic failure after its internal attempt allowance.
- [CLI IPC implementation](https://github.com/Windscribe/Desktop-App/blob/master/src/windscribe-cli/backendcommander.cpp)
  communicates with the app and waits for acknowledgment/state; nonblocking dispatch does not create
  an isolated engine. No verified supported desktop-without-GUI control mode was established.
- [rpm-ostree administration](https://coreos.github.io/rpm-ostree/administrator-handbook/)
  documents staged deployments, package layering/overrides and rollback. Writable state does not
  roll back with the operating-system deployment.

[Bluefin's administration guide](https://docs.projectbluefin.io/es/administration/) describes
bootc deployment/rollback and discourages local layering; its
[custom-image guide](https://docs.projectbluefin.io/bluefin-dx/) documents a supported image template.
The migration must follow the installed Bluefin variant's supported workflow.

## Reversible sequence for a separately authorized maintenance window

1. Establish console access and a maintenance window with permission to disconnect and reboot.
   Record the current deployment/image digest, bootloader recovery path, Windscribe package version,
   package origin (layered or image-baked), and service unit ownership using read-only tools.
   Determine whether this Bluefin installation uses rpm-ostree package layering or a bootc image
   workflow. Do not combine package managers or assume layering is supported for every bootc image.
2. The user privately backs up Windscribe configuration and Auto-Windscribe XDG data with restricted
   permissions. Never upload it, extract authentication tokens, or include it in an OS image build.
   Keep the original desktop RPM/source available and retain/pin a bootable known-good deployment.
   Confirm a rollback boot is possible before making a replacement deployment.
3. Obtain the official Fedora x86_64 CLI-only RPM through Windscribe's current download page.
   Verify provenance/signature/checksum through vendor-published information. Inspect package name,
   version, dependencies, conflicts, file ownership and unit names before staging anything.
   Do not assume it co-installs with the desktop package or shares a compatible authenticated store.
4. For a **layered rpm-ostree** desktop package, stage removal of that exact layered package and
   addition of the inspected CLI-only RPM into one reviewed pending deployment. For an **image-baked**
   package, use a supported derived image or reviewed base removal override; do not force-overwrite
   RPM-owned paths. Review the complete pending deployment diff. Reboot rather than live-applying
   a replacement networking/helper stack. Exact transaction arguments depend on inspected metadata;
   prepare and approve them before execution, not by guessing package conflicts here.
5. For **bootc image-managed** installations, prefer a reproducible derived Bluefin image that removes
   the desktop RPM and installs the verified CLI package at build time. Pin the base and RPM artifact,
   test the image separately, publish no credentials, and stage through the supported Bluefin/bootc
   image update workflow. Keep the original image digest/deployment available for rollback. Avoid
   ad-hoc host layering if incompatible with that image's supported update path. Rebuild/revalidate
   the derived image for future base and Windscribe updates.
6. During the approved reboot/maintenance window, ensure the old desktop autostart and old engine do
   not compete with the new service. Inspect package-supplied unit compatibility and permissions;
   do not reuse guessed unit names or start two helpers. Follow vendor installation lifecycle,
   preserving firewall policy. No service migration is attempted by Auto-Windscribe.
7. Verify version/help/status/ports/catalog first. Pro entitlement belongs to the account, but
   authenticated-session reuse across packages is NOT guaranteed. If needed, the user signs in
   through the official CLI, without giving credentials to the agent. Never copy private keys or
   tokens to simulate a migrated session. Preserve the old backup untouched.
8. Translate only user-selected settings through the headless build's documented interfaces and
   version-specific reference. The official guide documents CLI-only configuration/reload and
   upstream CLI source distinguishes headless AmneziaWG preset instructions from GUI instructions.
   Do not copy desktop internals, infer SNI/MTU/TLS values, or promise all advanced options match.
   Confirm firewall, DNS, split tunneling, autoconnect and anti-censorship choices with the user.
9. Obtain a separate bounded connectivity-test authorization. Validate baseline, one connection,
   normal disconnect, late settlement, firewall behavior, and quit retention. Only then evaluate
   exhaustive scanning. Native deadlines and settlement still require observation; headless does
   not imply instant failure or reliable network restoration.
10. If validation fails, stop scanning, use the approved rollback deployment/image workflow and
    reboot. Restore the original package/autostart arrangement and user-managed private configuration
    backup if it changed. OS rollback alone does not restore home-directory or shared /var data.
    Recheck CLI status and firewall policy before any additional live test. Do not delete the
    known-good deployment until both rollback and ordinary updates have been validated.

This is a reviewable migration procedure, not an executable installer. No rpm-ostree/bootc
command, package replacement, service change, configuration migration or reboot was performed
in this development session. Migration requires a new explicit approval after host/package
inspection has made the exact transaction and rollback commands concrete.
