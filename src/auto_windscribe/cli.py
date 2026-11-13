"""Terminal entry point. No installation hooks, daemons, or automatic live consent."""

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from . import __version__
from .demo import demo_services
from .errors import AppError
from .health import NetworkHealthChecker, public_report
from .models import Candidate, Profile
from .ranking import make_queue, matches, rank_results
from .service import Services, build_services
from .signals import cancellation_signals
from .storage import Paths
from .windscribe import WindscribeCLI

console = Console(highlight=False)


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    # Suppressed defaults allow options both before and after a subcommand.
    for flag, help_text in (
        ("offline", "Cache only: no native CLI or network access"),
        ("dry-run", "Show planned actions; no native CLI or probes"),
        ("demo", "Isolated synthetic offline demo; no real VPN actions"),
        ("live", "Confirm a bounded live session; no persistent configuration required"),
        ("json", "Machine-readable JSON output"),
    ):
        common.add_argument(
            f"--{flag}", action="store_true", default=argparse.SUPPRESS, help=help_text
        )
    root = argparse.ArgumentParser(
        prog="auto-windscribe",
        parents=[common],
        description="Unofficial Linux controller for native Windscribe. "
        "Live actions require deliberate session confirmation. Run without a subcommand "
        "to open the TUI.",
    )
    root.add_argument(
        "--auto-scan",
        action="store_true",
        help="Offer Turbo on launch; --live explicitly consents to start",
    )
    root.add_argument("--version", action="version", version=__version__)
    sub = root.add_subparsers(dest="command")
    for name, help_text in (
        ("status", "Read connection status without exposing IP addresses"),
        ("protocols", "Discover supported protocols and ports"),
        ("network-check", "Bounded network probes; confirmation required"),
        ("recovery-status", "Read the last saved recovery report"),
        ("doctor", "Read-only diagnostics; never runs connectivity probes"),
    ):
        sub.add_parser(name, parents=[common], help=help_text)
    locations = sub.add_parser("locations", parents=[common], help="Discover or browse locations")
    locations.add_argument("--search", default="")
    locations.add_argument("--countries", action="store_true")
    locations.add_argument("--cities", action="store_true")
    locations.add_argument("--tier", choices=["all", "free", "pro", "unknown"], default="all")
    scan = sub.add_parser("scan", parents=[common], help="Plan or run a bounded sequential scan")
    scan.add_argument(
        "--gui-restart-recovery",
        dest="gui_restart",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Opt in to verified GUI restart; disable Auto-connect in official GUI first",
    )
    scan.add_argument("--gui-restart-limit", type=int)
    scan.add_argument("--gui-stop-timeout", type=float)
    scan.add_argument("--gui-start-timeout", type=float)
    scan.add_argument("--auto-recover", action=argparse.BooleanOptionalAction, default=None)
    scan.add_argument("--settlement-timeout", type=float)
    scan.add_argument("--recovery-timeout", type=float)
    scan.add_argument(
        "--continue-after-success", action=argparse.BooleanOptionalAction, default=None
    )
    scan.add_argument("--max-recoveries", type=int)
    scan.add_argument("--recovery-delay", type=float)
    scan.add_argument("--profile", default=None)
    scan.add_argument("--max-tests", type=int)
    scan.add_argument("--mode", choices=["first-success", "rank", "fast"])
    scan.add_argument("--search", default="")
    scan.add_argument("--country", action="append")
    scan.add_argument("--city", action="append")
    scan.add_argument("--location", action="append")
    scan.add_argument("--protocol", action="append")
    scan.add_argument("--port", type=int, action="append")
    scan.add_argument("--preflight-concurrency", type=int)
    scan.add_argument("--preflight-budget", type=int)
    scan.add_argument("--preflight-timeout", type=float)
    scan.add_argument("--session-budget", type=float)
    scan.add_argument("--resume", type=int, metavar="SCAN_ID")
    results = sub.add_parser(
        "results", parents=[common], help="History, rankings and resumable scans"
    )
    results.add_argument(
        "--sort", choices=["score", "rtt", "loss", "connect", "success", "recent"], default="score"
    )
    results.add_argument(
        "--profile", default=None, help="Filter to this profile's network/settings context"
    )
    results.add_argument("--manifest", type=int, metavar="SCAN_ID")
    results.add_argument("--history", action="store_true")
    results.add_argument("--scans", action="store_true")
    connect = sub.add_parser(
        "connect", parents=[common], help="Connect a specific discovered nickname"
    )
    connect.add_argument("location")
    connect.add_argument("--protocol", required=True)
    connect.add_argument("--port", type=int, required=True)
    connect.add_argument("--profile", default=None)
    connect.add_argument(
        "--interrupt", action="store_true", help="Also authorize replacing an existing connection"
    )
    sub.add_parser("disconnect", parents=[common], help="Explicitly disconnect the system VPN")
    sub.add_parser(
        "recover-gui", parents=[common], help="Explicit bounded GUI restart; never helpers"
    )
    retry = sub.add_parser(
        "retry-deferred", parents=[common], help="Explicitly requeue settled UNKNOWN candidates"
    )
    retry.add_argument("scan_id", type=int)
    profiles = sub.add_parser(
        "profiles", parents=[common], help="List, show, save or delete app profiles"
    )
    profiles.add_argument(
        "action", choices=["list", "show", "save", "delete"], nargs="?", default="list"
    )
    profiles.add_argument("name", nargs="?")
    profiles.add_argument("--file", type=Path, help="Validated profile JSON to save")
    return root


def output(data: Any, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        table = Table(show_lines=False, expand=True)
        keys = list(data[0])
        for key in keys:
            table.add_column(key.replace("_", " ").title(), overflow="fold")
        for row in data:
            table.add_row(
                *(str(row.get(key, "—")) if row.get(key) is not None else "—" for key in keys)
            )
        console.print(table)
    else:
        console.print_json(json.dumps(data, ensure_ascii=False))


def scan_profile(args: argparse.Namespace, services: Services) -> Profile:
    profile = services.profile(args.profile)
    changes = {
        field: getattr(args, arg)
        for arg, field in (
            ("gui_restart", "gui_restart"),
            ("gui_restart_limit", "gui_restart_limit"),
            ("gui_stop_timeout", "gui_stop_timeout"),
            ("gui_start_timeout", "gui_start_timeout"),
            ("auto_recover", "auto_recover"),
            ("max_recoveries", "max_recoveries"),
            ("settlement_timeout", "settlement_timeout"),
            ("recovery_timeout", "recovery_timeout"),
            ("continue_after_success", "continue_after_success"),
            ("recovery_delay", "recovery_delay"),
            ("max_tests", "max_tests"),
            ("preflight_concurrency", "preflight_concurrency"),
            ("preflight_budget", "preflight_budget"),
            ("preflight_timeout", "preflight_timeout"),
            ("session_budget", "session_budget"),
            ("mode", "mode"),
            ("country", "countries"),
            ("city", "cities"),
            ("location", "locations"),
            ("protocol", "protocols"),
        )
        if getattr(args, arg) is not None
    }
    if args.port:
        changes["ports"] = {p: args.port for p in (args.protocol or profile.protocols)}
    return replace(profile, **changes).validate()


def confirm_session(args: argparse.Namespace, services: Services, message: str) -> None:
    gate = services.controller.gate
    if (
        not gate.confirmed
        and not services.offline
        and sys.stdin.isatty()
        and not getattr(args, "json", False)
    ):
        console.print(message, markup=False)
        gate.confirmed = input("Continue? [y/N] ").strip().lower() in ("y", "yes")
    gate.require_live()


async def dispatch(args: argparse.Namespace, services: Services) -> int:
    machine = getattr(args, "json", False)
    dry = getattr(args, "dry_run", False)
    cmd = args.command
    rows: list[dict[str, Any]]
    if cmd is None:
        from .tui import WindscribeApp

        app = WindscribeApp(
            services,
            dry_run=dry,
            auto_start=getattr(args, "auto_scan", False) and getattr(args, "live", False),
        )
        with cancellation_signals(app.request_exit):
            await app.run_async()
        return 0
    if cmd == "status":
        output(asdict(await services.vpn.status()), machine)
    elif cmd in ("locations", "protocols"):
        catalog = await services.catalog()
        if cmd == "protocols":
            rows = [{"protocol": p, "ports": ports} for p, ports in catalog.capabilities.items()]
        else:
            locs = [
                loc
                for loc in catalog.locations
                if matches(loc, Profile(tier=args.tier), args.search)
            ]
            rows = [asdict(loc) for loc in locs]
            if args.countries or args.cities:
                key = "country" if args.countries else "city"
                rows = [{key: value} for value in sorted({getattr(loc, key) for loc in locs})]
        output(
            {"stale": catalog.stale, "warning": catalog.warning, "items": rows}
            if machine
            else rows,
            machine,
        )
        if catalog.stale and not machine:
            console.print("Cached catalog is outdated or refresh failed.")
    elif cmd == "recovery-status":
        output(services.store.recovery_status(), machine)
    elif cmd == "network-check":
        if dry or services.offline:
            output(NetworkHealthChecker.plan(services.profile()), machine)
        else:
            confirm_session(args, services, "Run bounded Google ping, DNS and HTTPS health checks?")
            assert services.controller.health is not None
            output(
                public_report(await services.controller.health.check(services.profile())), machine
            )
    elif cmd == "doctor":
        status = await services.vpn.status()
        checks: dict[str, Any] = {
            "app_version": __version__,
            "mode": "offline" if services.offline else "read-only",
            "consent": "session confirmation or --live",
            "preflight": "unavailable: native CLI does not expose trusted endpoint mapping",
            "status": asdict(status),
            "firewall_policy": "preserved",
            "gui_dialog": "Manual-mode failure prompts cannot be suppressed by this controller",
            "recovery_cycles": "Unavailable: no GUI preference restore or exclusive IPC lease",
            "advanced_settings": "Not exposed by CLI; see docs/CAPABILITIES.md",
        }
        if isinstance(services.vpn, WindscribeCLI):
            checks["cli_version"] = await services.vpn.version()
            checks["protocols"] = await services.vpn.capabilities()
            try:
                checks["available_presets"] = await services.vpn.presets()
            except AppError:
                checks["available_presets"] = "unavailable"
        output(checks, machine)
    elif cmd == "profiles":
        profiles = services.profiles
        if args.action == "list":
            output(
                [
                    {
                        "name": p.name,
                        "mode": p.mode,
                        "max_tests": p.max_tests,
                        "protocols": ", ".join(p.protocols),
                    }
                    for p in profiles.all().values()
                ],
                machine,
            )
        else:
            if not args.name:
                raise ValueError("A profile name is required")
            if args.action == "show":
                output(asdict(profiles.get(args.name)), machine)
            elif args.action == "save":
                if not args.file:
                    raise ValueError("Use --file with a profile JSON document")
                profile = Profile.from_dict(
                    {**json.loads(args.file.read_text()), "name": args.name}
                )
                if not dry and (not services.offline or services.demo):
                    profiles.save(profile)
                output(asdict(profile), machine)
            elif args.action == "delete":
                if not dry and (not services.offline or services.demo):
                    profiles.delete(args.name)
                output(
                    {
                        "profile": args.name,
                        "deleted": not dry and (not services.offline or services.demo),
                    },
                    machine,
                )
    elif cmd == "results":
        if args.manifest is not None:
            output(services.store.manifest(args.manifest), machine)
        elif args.scans:
            output(services.store.scans(), machine)
        else:
            context = services.profile(args.profile).context if args.profile else None
            history = services.store.history(context)
            if args.history:
                output([asdict(r) for r in history], machine)
            else:
                # Never pool independent network/settings contexts in a reliability statistic.
                rows = []
                for ctx in sorted({r.context for r in history}):
                    for r in rank_results([h for h in history if h.context == ctx], args.sort):
                        rows.append(
                            {
                                "candidate": r.candidate.key,
                                "context": ctx,
                                "score": r.score,
                                "successes": f"{r.successes}/{r.attempts}",
                                "rtt_ms": r.verified.metrics.rtt_ms if r.verified else None,
                                "latest": r.latest.code,
                            }
                        )
                output(rows, machine)
    elif cmd == "recover-gui":
        if dry or services.offline:
            output(
                {
                    "action": "recover-gui",
                    "executed": False,
                    "requires": "verified GUI, logged-in Disconnected, firewall Off, settled IPC",
                },
                machine,
            )
        else:
            confirm_session(
                args,
                services,
                "Restart the verified Windscribe GUI and check internet? "
                "Disable Auto-connect in the official app first. "
                "Backend cleanup may affect connectivity. "
                "On/unknown firewall and existing tunnels are protected.",
            )
            report = await services.controller.recover_gui(
                replace(services.profile(), gui_restart=True)
            )
            output(asdict(report), machine)
            return 0 if report.safe else 1
    elif cmd == "retry-deferred":
        if dry or services.offline:
            output(
                {"action": "retry-deferred", "scan_id": args.scan_id, "executed": False}, machine
            )
        else:
            confirm_session(
                args,
                services,
                "Requeue safely deferred UNKNOWN candidates? Original results remain.",
            )
            count = services.controller.retry_deferred(args.scan_id)
            output({"scan_id": args.scan_id, "requeued": count}, machine)
    elif cmd == "disconnect":
        if dry:
            output({"action": "disconnect", "executed": False}, machine)
        else:
            confirm_session(
                args, services, "Disconnect the system VPN? Internet may be interrupted."
            )
            await services.controller.disconnect()
            output({"state": "disconnected"}, machine)
    elif cmd in ("connect", "scan"):
        catalog = await services.catalog()
        if cmd == "connect":
            locations = [
                loc
                for loc in catalog.locations
                if loc.nickname.casefold() == args.location.casefold()
            ]
            if len(locations) != 1 or not locations[0].available:
                raise ValueError("Choose one exact, available, unique nickname from 'locations'")
            candidate = Candidate(locations[0], args.protocol, args.port)
            if args.port not in catalog.capabilities.get(args.protocol, []):
                raise ValueError("Unsupported port; use 'protocols'")
            if dry:
                output(
                    {"action": "connect", "candidate": asdict(candidate), "executed": False},
                    machine,
                )
            else:
                confirm_session(
                    args,
                    services,
                    f"Connect {candidate.key} and verify HTTPS?"
                    + (
                        " Replace the current tunnel."
                        if args.interrupt
                        else " Existing tunnel is protected."
                    ),
                )
                result = await services.controller.connect(
                    candidate, services.profile(args.profile), args.interrupt
                )
                output(asdict(result), machine)
                return 0 if result.outcome == "success" else 1
        else:
            if args.resume:
                row = services.store.scan(args.resume)
                queue = [c for _, c in services.store.pending(args.resume)]
                profile = Profile.from_dict(json.loads(row["profile"]))
                if args.gui_restart is not None:
                    profile = replace(profile, gui_restart=args.gui_restart)
                scan_id = args.resume
            else:
                profile = scan_profile(args, services)
                queue = make_queue(
                    catalog, profile, services.store.history(profile.context), args.search
                )
                scan_id = None
            if not queue:
                raise AppError(
                    "empty_queue", "No candidates match, or all are in failure cooldown."
                )
            if dry:
                output(
                    {
                        "executed": False,
                        "profile": asdict(profile),
                        "queue": [asdict(c) for c in queue],
                    },
                    machine,
                )
            else:
                confirm_session(
                    args,
                    services,
                    f"Start {profile.strategy}: up to {profile.max_tests} VPN attempts, "
                    f"{profile.preflight_budget} transport probes, "
                    f"{profile.session_budget:g}s session? "
                    "Includes baseline probes and bounded recovery of scan-owned tunnels. "
                    "Internet may be interrupted; existing tunnel is protected."
                    + (
                        f" GUI restart enabled (limit {profile.gui_restart_limit}); "
                        "disable Auto-connect in the official app first."
                        if profile.gui_restart
                        else " GUI restart disabled."
                    ),
                )
                if scan_id is None:
                    scan_id = services.store.new_scan(queue, profile)
                if not machine:
                    services.controller.emit = lambda e: console.print(
                        e.phase, e.message, markup=False
                    )
                await services.controller.scan(scan_id, catalog, gui_restart=profile.gui_restart)
                row = services.store.scan(scan_id)
                output(
                    {"scan_id": scan_id, "state": row["state"], "attempts": row["attempts"]},
                    machine,
                )
                return 1 if row["state"] == "paused" else 0
    return 0


async def execute(args: argparse.Namespace, services: Services) -> int:
    task = asyncio.current_task()
    assert task is not None
    if args.command is None:
        return await dispatch(args, services)
    with cancellation_signals(task.cancel):
        try:
            return await dispatch(args, services)
        except asyncio.CancelledError:
            output(
                {"state": "cancelled", "message": "Stopped. The system VPN state was retained."},
                getattr(args, "json", False),
            )
            return 130


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="auto-windscribe-demo-") as temporary:
            services = (
                demo_services(Paths.under(Path(temporary)))
                if getattr(args, "demo", False)
                else build_services(
                    Paths.discover(),
                    offline=getattr(args, "offline", False) or getattr(args, "dry_run", False),
                    confirmed=getattr(args, "live", False),
                    readonly=args.command
                    in (
                        "status",
                        "doctor",
                        "locations",
                        "protocols",
                        "results",
                        "recovery-status",
                        "network-check",
                    )
                    or (args.command == "profiles" and args.action in ("list", "show")),
                )
            )
            try:
                return asyncio.run(execute(args, services))
            finally:
                services.store.close()
    except (AppError, ValueError, OSError) as exc:
        if getattr(args, "json", False):
            output({"error": getattr(exc, "code", "invalid_input"), "message": str(exc)}, True)
        else:
            console.print(f"Error: {exc}", style="red", markup=False)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
