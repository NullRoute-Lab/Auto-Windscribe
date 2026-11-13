# Third-party acknowledgments

Auto-Windscribe is an original implementation by NullRoute Lab, licensed under the standard MIT
License in LICENSE. The project is not affiliated with or endorsed by Windscribe Limited.
Windscribe is the name/trademark of its owner; the official client is separately installed and is
not redistributed here.

## Architectural references reviewed

- [AutoScribe](https://github.com/rasool-mrr/AutoScribe), copyright 2026 Russell.
  The supplied source uses **MIT License (with Removal-on-Request Condition)**, including a
  requirement to take down copies/derivative work within 30 days of a copyright-holder request.
  This is not the standard MIT license. Its setup, filtering, history and stop-on-success concepts
  informed the requirements review. No source, translations, assets or bundled catalogs were
  copied, adapted or distributed. Its firewall-disabling behavior was explicitly rejected.
- [Windscribe-Analyzer](https://github.com/AminDenizer/Windscribe-Analyzer), copyright 2026
  amin denizer, standard MIT. Reviewed Go orchestration, nickname resolution, queue state,
  catalog caching, context tagging and reporting concepts. No source, assets or preset data were
  copied or distributed. Direct config writes, helper termination, static fallback locations and
  other-platform implementation paths were explicitly rejected.

The supplied extracted reference directories were removed only after this review and original
implementation. The durable comparison is in docs/ARCHITECTURE.md.

## Runtime dependencies and interfaces

- [Textual](https://github.com/Textualize/textual) and its Rich terminal stack, MIT; installed as
  Python dependencies with their original package metadata and license notices.
- Python's standard library provides asyncio, SQLite, argparse and the other core facilities.
- Native Windscribe CLI documentation and public source were consulted as interface references,
  not copied into this repository. Links are in docs/ARCHITECTURE.md and docs/CAPABILITIES.md.
- Existing Linux `ip`, `ping` and `curl` tools are optional runtime probe dependencies, not bundled.

The lockfile records exact Python dependency versions. Dependency code is not vendored in Git.

The v0.2 design also consulted public Windscribe Desktop-App and wsnet source for endpoint
inventory/port-map semantics and official Windscribe proxy documentation. No source code,
credentials, catalogs, node addresses or protocol presets from those projects are bundled.
Pinned research references are in docs/TURBO.md. Linux manual pages informed the future namespace
analysis; no privileged namespace implementation is included.
