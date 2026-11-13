"""Keyboard-first Textual interface; all I/O runs in cancellable async workers."""

import asyncio
import time
from dataclasses import asdict, replace
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from .engine import Progress
from .errors import AppError
from .i18n import _
from .models import PROTOCOLS, Candidate, Profile, State, Status
from .ranking import fuzzy_score, make_queue, matches, rank_results
from .service import Services
from .storage import Catalog, atomic_json


class Confirm(ModalScreen[bool]):
    DEFAULT_CSS = """
    Confirm { align: center middle; background: $background 65%; }
    Confirm > Vertical { width: 65; height: auto; border: thick $warning; padding: 1 2; }
    Confirm Horizontal { height: 3; align-horizontal: right; margin-top: 1; }
    Confirm Button { margin-left: 1; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.message, markup=False)
            with Horizontal():
                yield Button(_("Cancel"), id="cancel")
                yield Button(_("Confirm"), variant="warning", id="confirm")

    def on_mount(self) -> None:
        self.query_one("#confirm", Button).focus()

    @on(Button.Pressed)
    def clicked(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class Preferences(ModalScreen[Profile | None]):
    DEFAULT_CSS = """
    Preferences { align: center middle; background: $background 70%; }
    Preferences > Vertical { width: 78; height: 90%; border: thick $accent; padding: 1 2; }
    Preferences VerticalScroll { height: 1fr; }
    Preferences Label { margin-top: 1; }
    Preferences Input, Preferences Select { width: 100%; }
    Preferences #error { color: $error; height: auto; }
    Preferences Horizontal { height: 3; align-horizontal: right; }
    """
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        profile: Profile,
        section: str = "all",
        capabilities: dict[str, list[int]] | None = None,
    ) -> None:
        super().__init__()
        self.profile, self.section = profile, section
        self.capabilities = capabilities or {}

    def compose(self) -> ComposeResult:
        p = self.profile
        with Vertical():
            yield Static(_("Application preferences · Windscribe GUI settings are preserved"))
            with VerticalScroll():
                fields = [
                    ("name", "Profile name", p.name),
                    (
                        "countries",
                        "Countries / regions (comma separated; empty = all)",
                        ", ".join(p.countries),
                    ),
                    ("cities", "Cities (comma separated)", ", ".join(p.cities)),
                    ("locations", "Nicknames (comma separated)", ", ".join(p.locations)),
                    (
                        "protocols",
                        "Protocols in priority order (comma separated)",
                        ", ".join(p.protocols),
                    ),
                    ("max_tests", "Maximum attempts, including retries", str(p.max_tests)),
                    ("timeout", "Connection timeout in seconds", str(p.timeout)),
                    ("retries", "Retries per failed candidate (0–2)", str(p.retries)),
                    ("session_budget", "Session time budget (seconds)", str(p.session_budget)),
                    (
                        "preflight_concurrency",
                        "Preflight concurrency (1–64)",
                        str(p.preflight_concurrency),
                    ),
                    (
                        "preflight_budget",
                        "Preflight probe budget (including retries)",
                        str(p.preflight_budget),
                    ),
                    ("network_label", "Private network label (no SSID/IP needed)", p.network_label),
                    (
                        "settings_label",
                        "Settings context label (change after manual GUI changes)",
                        p.settings_label,
                    ),
                ]
                for key, label, value in fields:
                    yield Label(_(label))
                    yield Input(value, id=f"pref-{key}")
                for proto in PROTOCOLS:
                    allowed = (
                        ", ".join(map(str, self.capabilities.get(proto, []))) or "not discovered"
                    )
                    yield Label(f"{proto} ports · supported: {allowed} · blank = all supported")
                    yield Input(
                        ", ".join(map(str, p.ports.get(proto, []))), id=f"pref-ports-{proto}"
                    )
                yield Label(_("Mode"))
                yield Select(
                    [(v, v) for v in ("first-success", "rank", "fast")],
                    value=p.mode,
                    allow_blank=False,
                    id="pref-mode",
                )
                yield Label(_("Server tier (unknown means CLI does not disclose it)"))
                yield Select(
                    [(v, v) for v in ("all", "free", "pro", "unknown")],
                    value=p.tier,
                    allow_blank=False,
                    id="pref-tier",
                )
                yield Label(_("Optional 1 MB download measurement (off by default)"))
                yield Switch(p.download, id="pref-download")
            yield Static("", id="error", markup=False)
            with Horizontal():
                yield Button(_("Cancel"), id="cancel")
                yield Button(_("Apply & save"), variant="primary", id="apply")

    def on_mount(self) -> None:
        key = {"filters": "countries", "protocols": "protocols", "profiles": "name"}.get(
            self.section, "name"
        )
        self.query_one(f"#pref-{key}", Input).focus()

    @on(Button.Pressed)
    def clicked(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return

        def value(key: str) -> str:
            return self.query_one(f"#pref-{key}", Input).value.strip()

        def items(key: str) -> list[str]:
            return [v.strip() for v in value(key).split(",") if v.strip()]

        try:
            protocols = items("protocols")
            ports = {
                proto: [int(port) for port in items(f"ports-{proto}")]
                for proto in PROTOCOLS
                if items(f"ports-{proto}")
            }
            for proto in protocols:
                if self.capabilities and proto not in self.capabilities:
                    raise ValueError(f"{proto} is not supported by the installed CLI")
                if self.capabilities and any(
                    port not in self.capabilities[proto] for port in ports.get(proto, [])
                ):
                    raise ValueError(f"Unsupported {proto} port; see its discovered list")
            result = replace(
                self.profile,
                name=value("name"),
                countries=items("countries"),
                cities=items("cities"),
                locations=items("locations"),
                protocols=protocols,
                ports=ports,
                max_tests=int(value("max_tests")),
                timeout=float(value("timeout")),
                retries=int(value("retries")),
                session_budget=float(value("session_budget")),
                preflight_concurrency=int(value("preflight_concurrency")),
                preflight_budget=int(value("preflight_budget")),
                network_label=value("network_label"),
                settings_label=value("settings_label"),
                mode=str(self.query_one("#pref-mode", Select).value),
                tier=str(self.query_one("#pref-tier", Select).value),
                download=self.query_one("#pref-download", Switch).value,
            ).validate()
            self.dismiss(result)
        except ValueError as exc:
            self.query_one("#error", Static).update(str(exc))

    def action_cancel(self) -> None:
        self.dismiss(None)


class Help(ModalScreen[None]):
    DEFAULT_CSS = """
    Help { align: center middle; background: $background 75%; }
    Help > VerticalScroll { width: 76; height: 85%; border: thick $accent; padding: 1 2; }
    """
    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close")]

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(
                _(
                    "AUTO-WINDSCRIBE · Keyboard help\n\n"
                    "/ or Ctrl+F   Fuzzy search countries, cities and nicknames\n"
                    "↑ ↓ / PgUp PgDn   Navigate the candidate table\n"
                    "Space   Mark several combinations for a targeted scan\n"
                    "Enter   Confirm connection to the selected combination\n"
                    "R   Scan marked candidates, or the filtered view\n"
                    "F   Country, city, nickname and tier filters\n"
                    "P   Protocol / port and scan preferences\n"
                    "S   Save preferences as a named profile\n"
                    "O   Cycle sort order (or click a column heading)\n"
                    "D   Confirm system VPN disconnection\n"
                    "X   Stop work and retain the current system VPN state\n"
                    "U   Refresh the official catalog\n"
                    "A   Toggle the startup scan offer (confirmation still required)\n"
                    "Q / Ctrl+C   Stop work, quit, retain the tunnel\n"
                    "Esc   Close a dialog or return focus to the table\n\n"
                    "Live actions require one session confirmation. A scan starts only "
                    "when disconnected. Ranking scans disconnect their own connection between "
                    "candidates; the last connection is retained. Stop/Quit never disconnect. "
                    "A pending request can still finish after cancellation.\n\n"
                    "A dash means unmeasured, not zero. Scores use historical success, loss, "
                    "RTT, jitter, establishment time and freshness. ICMP blocking alone does "
                    "not imply a broken tunnel. CLI status may confirm only the city, so "
                    "the selected nickname is recorded as the requested endpoint.\n\n"
                    "Advanced GUI preferences remain under the official app's control. "
                    "Use doctor and docs/CAPABILITIES.md for the supported surface."
                ),
                markup=False,
            )
            yield Button(_("Close"), id="close")

    @on(Button.Pressed)
    def action_close(self) -> None:
        self.dismiss(None)


class WindscribeApp(App[None]):
    TITLE = "Auto-Windscribe"
    SUB_TITLE = "Linux terminal controller"
    CSS = """
    Screen { background: #0b1220; }
    #status { height: 3; padding: 1 2; background: #172338; color: #95efd7; }
    #overview { height: 2; padding: 0 2; color: #a8b9ce; }
    #toolbar { height: 3; margin: 0 1; }
    #search { width: 1fr; }
    #profile { width: 23; }
    #sort { width: 20; }
    TabbedContent { height: 1fr; margin: 0 1; }
    TabPane { padding: 0; }
    DataTable { height: 1fr; }
    #actions { height: 3; margin: 0 1; }
    #actions Button { min-width: 10; width: 1fr; margin: 0 1 0 0; }
    #progress { height: 1; margin: 0 2; }
    #message { height: 2; padding: 0 2; color: #a8b9ce; }
    #details { height: 3; padding: 0 1; background: #172338; }
    RichLog { height: 1fr; }
    """
    BINDINGS = [
        Binding("slash,ctrl+f", "search", "Search"),
        Binding("r", "scan", "Rescan"),
        Binding("f", "filters", "Filters"),
        Binding("p", "preferences", "Protocols"),
        Binding("s", "profiles", "Profiles"),
        Binding("a", "auto_scan", "Launch prompt", show=False),
        Binding("o", "sort", "Sort", show=False),
        Binding("space", "mark", "Mark", show=False),
        Binding("d", "disconnect", "Disconnect", show=False),
        Binding("x", "stop", "Stop"),
        Binding("u", "refresh", "Refresh", show=False),
        Binding("question_mark,f1", "help", "Help"),
        Binding("escape", "back", "Back", show=False),
        Binding("q", "quit", "Quit · keep VPN"),
        Binding("ctrl+c", "quit", "Quit · keep VPN", priority=True, show=False),
    ]

    def __init__(self, services: Services, dry_run: bool = False, auto_start: bool = False) -> None:
        super().__init__()
        self.services, self.dry_run = services, dry_run
        self.auto_start = auto_start
        self.profile = services.profile()
        self.catalog_data: Catalog | None = None
        self.status_data = Status()
        self.candidates: dict[str, Candidate] = {}
        self.marked: set[str] = set()
        self.operation = False
        self.sort_order = "score"
        self.history_rows: dict[str, Any] = {}
        self.services.controller.emit = self.progress_event
        self._initialized = False
        self._status_pending = False
        self._last_render = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(_("Loading read-only connection status…"), id="status", markup=False)
        yield Static("", id="overview", markup=False)
        with Horizontal(id="toolbar"):
            yield Input(placeholder=_("/ Search country, city or nickname…"), id="search")
            yield Select(
                [(p, p) for p in self.services.profiles.all()],
                value=self.profile.name,
                allow_blank=False,
                id="profile",
            )
            yield Select(
                [
                    (label, value)
                    for label, value in (
                        ("Overall score", "score"),
                        ("Lowest RTT", "rtt"),
                        ("Packet loss", "loss"),
                        ("Connect time", "connect"),
                        ("Success rate", "success"),
                        ("Recent success", "recent"),
                        ("Location", "location"),
                    )
                ],
                value="score",
                allow_blank=False,
                id="sort",
            )
        with TabbedContent():
            with TabPane(_("Servers"), id="servers-tab"):
                yield DataTable(id="servers", cursor_type="row", zebra_stripes=True)
                yield Static(
                    _("Select a combination to see its latest diagnostic."),
                    id="details",
                    markup=False,
                )
            with TabPane(_("Leaderboard"), id="best-tab"):
                yield DataTable(id="best", cursor_type="row", zebra_stripes=True)
            with TabPane(_("Diagnostics"), id="log-tab"):
                yield RichLog(id="log", highlight=False, markup=False, wrap=True, max_lines=500)
        yield ProgressBar(total=1, show_eta=False, id="progress")
        yield Static(
            _("READY · Confirm a bounded Turbo scan to begin. TCP evidence is not VPN Ping."),
            id="message",
            markup=False,
        )
        with Horizontal(id="actions"):
            for name, label, variant in (
                ("connect", "Connect", "primary"),
                ("disconnect", "Disconnect", "warning"),
                ("scan", "Rescan", "default"),
                ("resume", "Resume", "default"),
                ("stop", "Pause / Stop", "default"),
                ("help", "Help", "default"),
            ):
                yield Button(_(label), id=name, variant=variant)  # type: ignore[arg-type]
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#servers", DataTable).add_columns(
            "Pick",
            "Country / region",
            "City",
            "Nickname",
            "Protocol",
            "Port",
            "Outcome",
            "VPN Ping ms",
            "Score",
            "Preflight",
            "TCP ms",
        )
        self.query_one("#best", DataTable).add_columns(
            "Requested endpoint",
            "Score",
            "Success",
            "VPN Ping ms",
            "Loss %",
            "Jitter ms",
            "Connect s",
        )
        self.query_one("#servers", DataTable).focus()
        self._initialized = True
        self.load_catalog(initial=True)
        self.refresh_status()
        self.set_interval(3, self.refresh_status)

    def message(self, text: str) -> None:
        self.query_one("#message", Static).update(text)
        self.query_one("#log", RichLog).write(text)

    @work(exclusive=True, group="catalog", exit_on_error=False)
    async def load_catalog(self, initial: bool = False) -> None:
        try:
            self.catalog_data = await self.services.catalog()
            self.render_tables()
            if self.catalog_data.warning:
                self.message(self.catalog_data.warning)
            if (
                initial
                and not self.services.offline
                and not self.dry_run
                and (self.services.config.auto_scan or self.auto_start)
            ):
                status = await self.services.vpn.status()
                if status.state == State.DISCONNECTED and not self.dry_run:
                    if self.auto_start:
                        self.start_scan()
                    else:
                        self.action_scan()
                else:
                    self.message(
                        "Automatic scan skipped: existing/unknown connection is protected."
                    )
        except AppError as exc:
            self.message(str(exc))

    @work(group="status", exit_on_error=False)
    async def refresh_status(self) -> None:
        if self._status_pending:
            return
        self._status_pending = True
        try:
            self.status_data = await self.services.vpn.status()
            s = self.status_data
            mode = (
                "SIMULATED DEMO"
                if self.services.demo
                else "OFFLINE"
                if self.services.offline
                else ("SCANNING" if self.operation else "READY")
            )
            self.query_one("#status", Static).update(
                f"{mode}  │  {s.state.value.upper()}  {s.location}  {s.protocol}"
                f"{':' + str(s.port) if s.port else ''}  │  Firewall: {s.firewall}"
            )
        except AppError as exc:
            self.status_data = Status()
            self.query_one("#status", Static).update(f"ERROR · STATUS UNKNOWN · {exc}")
        finally:
            self._status_pending = False

    def render_tables(self) -> None:
        if not self.catalog_data:
            return
        catalog = self.catalog_data
        query = self.query_one("#search", Input).value
        table = self.query_one("#servers", DataTable)
        selected_key = self.selected_key()
        table.clear()
        self.candidates.clear()
        self.history_rows = {
            r.candidate.key: r
            for r in rank_results(
                self.services.store.history(self.profile.context),
                self.sort_order if self.sort_order != "location" else "score",
            )
        }
        locations = [loc for loc in catalog.locations if matches(loc, self.profile, query)]
        locations.sort(key=lambda loc: -fuzzy_score(query, loc.search_text))
        rows = []
        for loc in locations:
            for proto in self.profile.protocols:
                supported = catalog.capabilities.get(proto, [])
                for port in self.profile.ports.get(proto, supported):
                    if port not in supported:
                        continue
                    c = Candidate(loc, proto, port)
                    self.candidates[c.key] = c
                    r = self.history_rows.get(c.key)
                    rows.append((c, r))
        positions = {key: i for i, key in enumerate(self.history_rows)}
        if self.sort_order == "location":
            rows.sort(key=lambda pair: pair[0].key)
        elif not query:
            rows.sort(key=lambda pair: positions.get(pair[0].key, len(positions)))
        matched_count = len(rows)
        rows = rows[:1000]  # Bound widget work; filters/search access the full inventory.
        for c, r in rows:
            metric = r.verified.metrics.rtt_ms if r and r.verified else None
            outcome = self.services.controller.candidate_states.get(
                c.key, r.latest.state_label if r else "untested"
            )
            if not c.location.available:
                outcome = "unavailable"
            evidence = self.services.controller.evidence.get(c.key)
            table.add_row(
                "●" if c.key in self.marked else "",
                c.location.region,
                c.location.city,
                c.location.nickname,
                c.protocol,
                str(c.port),
                outcome,
                f"{metric:.1f}" if metric is not None else "—",
                str(r.score) if r else "—",
                evidence.state.value if evidence else "untested",
                f"{evidence.tcp_ms:.1f}" if evidence and evidence.tcp_ms is not None else "—",
                key=c.key,
            )
        if any(c.key == selected_key for c, _ in rows):
            table.move_cursor(row=next(i for i, (c, _) in enumerate(rows) if c.key == selected_key))
        best = self.query_one("#best", DataTable)
        best.clear()
        for r in list(self.history_rows.values())[:100]:
            if not r.verified:
                continue
            m = r.verified.metrics

            def fmt(value: float | None) -> str:
                return f"{value:.1f}" if value is not None else "—"

            best.add_row(
                r.candidate.key,
                str(r.score),
                f"{r.successes}/{r.attempts}",
                fmt(m.rtt_ms),
                fmt(m.loss_pct),
                fmt(m.jitter_ms),
                fmt(r.verified.connect_s),
                key=r.candidate.key,
            )
        self.query_one("#overview", Static).update(
            f"{len(catalog.locations)} locations · "
            + " / ".join(f"{p}:{len(ports)} ports" for p, ports in catalog.capabilities.items())
            + " · "
            f"{len(rows)}/{matched_count} combinations shown · {len(self.marked)} marked · "
            f"catalog {'OUTDATED' if catalog.stale else 'known'} · profile: {self.profile.name}"
        )

    def selected_key(self) -> str | None:
        table = self.query_one("#servers", DataTable)
        if table.row_count:
            return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
        return None

    @on(Input.Changed, "#search")
    def search_changed(self) -> None:
        if self._initialized:
            self.render_tables()

    @on(Select.Changed, "#sort")
    def sort_changed(self, event: Select.Changed) -> None:
        self.sort_order = str(event.value)
        if self._initialized:
            self.render_tables()

    @on(Select.Changed, "#profile")
    def profile_changed(self, event: Select.Changed) -> None:
        if self.operation and str(event.value) != self.profile.name:
            self.message("Pause before changing profile; the active scan keeps its saved profile.")
            self.query_one("#profile", Select).value = self.profile.name
            return
        if self._initialized and event.value != Select.BLANK:
            self.profile = self.services.profile(str(event.value))
            self.marked.clear()
            self.render_tables()

    @on(DataTable.HeaderSelected, "#servers")
    def header_selected(self, event: DataTable.HeaderSelected) -> None:
        order = {7: "rtt", 8: "score"}.get(event.column_index, "location")
        self.query_one("#sort", Select).value = order

    @on(DataTable.RowHighlighted, "#servers")
    def highlight(self, event: DataTable.RowHighlighted) -> None:
        key = str(event.row_key.value)
        row = self.history_rows.get(key)
        self.query_one("#details", Static).update(
            f"{key}\n{row.latest.phase} / {row.latest.code}: {row.latest.detail}\n"
            f"Times: {row.latest.timings} · States: {', '.join(row.latest.observations)}"
            if row
            else f"{key}\nUntested; quality is unknown."
        )

    @on(DataTable.RowSelected)
    def row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        candidate = self.candidates.get(key)
        if candidate is None and key in self.history_rows:
            candidate = self.history_rows[key].candidate
        if candidate:
            self.confirm_connect(candidate)

    @on(Button.Pressed)
    def button(self, event: Button.Pressed) -> None:
        actions = {
            "connect": self.action_connect,
            "disconnect": self.action_disconnect,
            "scan": self.action_scan,
            "resume": self.action_resume,
            "stop": self.action_stop,
            "help": self.action_help,
        }
        if event.button.id in actions:
            actions[event.button.id]()

    def allowed(self) -> bool:
        if self.operation:
            self.message("An operation is running. Stop it before starting another.")
            return False
        if self.dry_run:
            return True
        if self.services.offline and not self.services.demo:
            self.message("OFFLINE · Browsing only; no connection or probes.")
            return False
        return True

    def confirm_connect(self, candidate: Candidate) -> None:
        if not self.allowed():
            return
        if not self.catalog_data or not any(
            loc.key == candidate.location.key and loc.available
            for loc in self.catalog_data.locations
        ):
            self.message("Endpoint unavailable in the current catalog. Refresh first.")
            return
        if self.dry_run:
            self.message(f"DRY RUN · Would connect to {candidate.key}. Nothing executed.")
            return
        # Connection replacement requires a separate explicit Disconnect action.
        self.push_screen(
            Confirm(
                f"Connect to {candidate.key} and verify tunnel quality? "
                "This changes the system VPN and may interrupt internet access. "
                "An existing connection will be protected."
            ),
            lambda yes: self.launch("connect", candidate) if yes else None,
        )

    def action_connect(self) -> None:
        key = self.selected_key()
        if key and key in self.candidates:
            self.confirm_connect(self.candidates[key])

    def queue(self) -> list[Candidate]:
        if not self.catalog_data:
            return []
        catalog = self.catalog_data
        queue = make_queue(
            catalog,
            self.profile,
            self.services.store.history(self.profile.context),
            self.query_one("#search", Input).value,
            self.marked or None,
        )
        if self.marked:
            queue = [c for c in queue if c.key in self.marked]
        return queue

    def action_scan(self) -> None:
        if not self.allowed():
            return
        try:
            queue = self.queue()
        except AppError as exc:
            self.message(str(exc))
            return
        if not queue:
            self.message("No eligible candidates; check filters, selection or failure cooldown.")
            return
        if self.dry_run:
            self.message(f"DRY RUN · {len(queue)} combinations; no connection or probes executed.")
            return
        self.push_screen(
            Confirm(
                f"Scan up to {self.profile.max_tests} attempts across {len(queue)} "
                f"combinations in {self.profile.strategy} mode, up to "
                f"{self.profile.preflight_budget} "
                f"transport probes, {self.profile.session_budget:g}s budget? Internet may "
                f"be interrupted. "
                "Only start while disconnected. Stop/Quit retain the current VPN state."
            ),
            lambda yes: self.start_scan(queue) if yes else None,
        )

    def start_scan(self, queue: list[Candidate] | None = None) -> None:
        if self.operation or not self.catalog_data:
            return
        try:
            queue = queue if queue is not None else self.queue()
            if not queue:
                return
            scan_id = self.services.store.new_scan(queue, self.profile)
            self.launch("scan", scan_id)
        except AppError as exc:
            self.message(str(exc))

    def action_resume(self) -> None:
        if not self.allowed():
            return
        scans = [
            row
            for row in self.services.store.scans()
            if row["state"] in ("paused", "pending", "running", "limited")
        ]
        if not scans:
            self.message("No paused scan to resume. Use Rescan for a fresh attempt budget.")
            return
        scan_id = scans[0]["id"]
        if self.dry_run:
            self.message(f"DRY RUN · Would resume scan {scan_id}.")
            return
        self.push_screen(
            Confirm(
                f"Resume scan {scan_id} with its saved profile and a new bounded session "
                "budget? Start disconnected; internet may be interrupted."
            ),
            lambda yes: self.launch("scan", scan_id) if yes else None,
        )

    def action_disconnect(self) -> None:
        if not self.allowed():
            return
        if self.dry_run:
            self.message("DRY RUN · Would disconnect. Nothing executed.")
            return
        self.push_screen(
            Confirm(
                "Disconnect the current system-wide VPN? This may interrupt internet "
                "access. Windscribe's firewall preferences will be preserved."
            ),
            lambda yes: self.launch("disconnect") if yes else None,
        )

    def launch(self, kind: str, value: Any = None) -> None:
        if self.operation:
            return
        self.services.controller.gate.confirmed = True
        self.operation = True
        self.run_worker(self.operate(kind, value), group="operation", exit_on_error=False)

    async def operate(self, kind: str, value: Any) -> None:
        try:
            if kind == "connect":
                result = await self.services.controller.connect(value, self.profile)
                self.message(f"{result.code}: {result.detail}")
            elif kind == "disconnect":
                await self.services.controller.disconnect()
                self.message("Disconnected; firewall preferences preserved.")
            elif self.catalog_data:
                await self.services.controller.scan(value, self.catalog_data)
                self.message(f"Scan {value}: {self.services.store.scan(value)['state']}.")
        except asyncio.CancelledError:
            self.message("Stopped. Current system VPN state retained; pending requests may finish.")
        except (AppError, ValueError, OSError) as exc:
            self.message(str(exc))
        finally:
            self.operation = False
            self.services.controller.gate.confirmed = False
            self.render_tables()
            self.refresh_status()

    def progress_event(self, event: Progress) -> None:
        self.message(f"{event.phase}: {event.message}")
        if event.result:
            self.query_one("#log", RichLog).write(
                f"{event.result.phase} / {event.result.code} · {event.result.timings} · "
                + ", ".join(event.result.observations)
            )
        if event.total:
            self.query_one("#progress", ProgressBar).update(
                total=event.total, progress=event.completed
            )
        if event.eta_s is not None:
            self.query_one("#message", Static).update(
                f"{event.phase} · session ETA ≈ {event.eta_s:.0f}s (observed pace)"
            )
        if (
            event.phase.startswith("VPN ")
            or event.result
            or (event.evidence and time.monotonic() - self._last_render > 0.25)
        ):
            self._last_render = time.monotonic()
            self.render_tables()

    def action_stop(self) -> None:
        self.services.controller.stop()
        self.message("Stopping application work; no disconnect requested.")

    def request_exit(self) -> None:
        self.services.controller.stop()
        self.exit()

    async def action_quit(self) -> None:
        self.request_exit()

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    async def action_back(self) -> None:
        self.query_one("#servers", DataTable).focus()

    def action_sort(self) -> None:
        orders = ["score", "rtt", "loss", "connect", "success", "recent", "location"]
        self.query_one("#sort", Select).value = orders[
            (orders.index(self.sort_order) + 1) % len(orders)
        ]

    def action_mark(self) -> None:
        key = self.selected_key()
        if key:
            self.marked.symmetric_difference_update({key})
            self.render_tables()

    def action_refresh(self) -> None:
        self.load_catalog()

    def preferences(self, section: str) -> None:
        if self.operation:
            self.message("Stop the current scan before editing its preferences.")
            return
        self.push_screen(
            Preferences(
                self.profile, section, self.catalog_data.capabilities if self.catalog_data else None
            ),
            self.save_profile,
        )

    def save_profile(self, profile: Profile | None) -> None:
        if profile is None:
            return
        try:
            self.services.profiles.save(profile)
            self.profile = profile
            selector = self.query_one("#profile", Select)
            selector.set_options([(p, p) for p in self.services.profiles.all()])
            selector.value = profile.name
            self.marked.clear()
            self.render_tables()
            self.message(f"Saved application profile '{profile.name}'.")
        except (AppError, OSError) as exc:
            self.message(str(exc))

    def action_filters(self) -> None:
        self.preferences("filters")

    def action_preferences(self) -> None:
        self.preferences("protocols")

    def action_profiles(self) -> None:
        self.preferences("profiles")

    def action_help(self) -> None:
        self.push_screen(Help())

    def action_auto_scan(self) -> None:
        if self.services.offline or self.dry_run or self.operation:
            self.message("Launch preference is unavailable during offline browsing or active work.")
            return
        self.services.config.auto_scan = not self.services.config.auto_scan
        atomic_json(self.services.paths.config / "config.json", asdict(self.services.config))
        self.message(
            "Offer Turbo on launch: "
            + str(self.services.config.auto_scan)
            + ". A session confirmation is always required."
        )
