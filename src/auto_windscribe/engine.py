"""Sequential controller with ownership checks and interruption-safe persistence."""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from .errors import AppError, SafetyError
from .models import Candidate, Metrics, Outcome, Profile, Result, State, Status
from .preflight import Evidence, Preflight
from .probes import Probe
from .ranking import adaptive_order, rank_results
from .safety import OperationLock, SafetyGate
from .storage import Catalog, Store
from .windscribe import VPN


@dataclass(frozen=True)
class Progress:
    phase: str
    completed: int = 0
    total: int = 0
    message: str = ""
    result: Result | None = None
    evidence: Evidence | None = None
    eta_s: float | None = None


def owns(status: Status, candidate: Candidate) -> bool:
    names = {
        candidate.location.city.casefold(),
        candidate.location.nickname.casefold(),
        f"{candidate.location.city} - {candidate.location.nickname}".casefold(),
        f"{candidate.location.region} - {candidate.location.city} - "
        f"{candidate.location.nickname}".casefold(),
    }
    return (
        status.state == State.CONNECTED
        and status.location.casefold() in names
        and status.protocol == candidate.protocol
        and status.port == candidate.port
    )


class Controller:
    def __init__(
        self,
        vpn: VPN,
        probe: Probe,
        store: Store,
        lock: OperationLock,
        gate: SafetyGate,
        poll_interval: float = 0.5,
        preflight: Preflight | None = None,
    ) -> None:
        self.vpn, self.probe, self.store = vpn, probe, store
        self.lock, self.gate = lock, gate
        self.poll_interval = poll_interval
        self.preflight = preflight
        self.evidence: dict[str, Evidence] = {}
        self.candidate_states: dict[str, str] = {}
        self.task: asyncio.Task | None = None
        self.owned: Candidate | None = None
        self.emit: Callable[[Progress], None] = lambda event: None

    def stop(self) -> None:
        # Stop and Quit do not dispatch disconnect, even if an IPC request is in flight.
        if self.task is not None:
            self.task.cancel()

    async def _disconnected(self) -> None:
        status = await self.vpn.status()
        if status.state != State.DISCONNECTED:
            raise SafetyError(
                "existing_connection",
                "Windscribe is not disconnected. "
                "Use an explicit Disconnect action before scanning.",
            )
        if status.logged_in is not True:
            raise SafetyError("logged_out", "Log in using the official Windscribe app.")

    async def _disconnect(self, expected: Candidate | None = None) -> None:
        status = await self.vpn.status()
        if expected is not None and not owns(status, expected):
            raise SafetyError(
                "state_conflict",
                "Connection changed outside this operation; "
                "no disconnect was sent. Review the official app.",
            )
        if status.state == State.DISCONNECTED:
            return
        if status.state == State.UNKNOWN:
            raise SafetyError("state_unknown", "Cannot safely control an unknown connection state.")
        await self.vpn.disconnect()
        self.owned = None
        try:
            async with asyncio.timeout(15):
                while True:
                    current = await self.vpn.status()
                    if current.state == State.DISCONNECTED:
                        return
                    if current.state not in (State.DISCONNECTING, State.CONNECTED):
                        raise SafetyError("state_conflict", "Unexpected state while disconnecting.")
                    if (
                        expected is not None
                        and current.state == State.CONNECTED
                        and not owns(current, expected)
                    ):
                        raise SafetyError("state_conflict", "Another connection replaced the scan.")
                    await asyncio.sleep(self.poll_interval)
        except TimeoutError as exc:
            raise AppError(
                "disconnect_timeout", "Disconnect did not settle; no helper was stopped."
            ) from exc

    async def disconnect(self) -> None:
        self.gate.require_live()
        with self.lock.hold():
            self.task = asyncio.current_task()
            try:
                await self._disconnect()
            finally:
                self.task = None

    async def _quality(self, candidate: Candidate, profile: Profile) -> Metrics:
        task = asyncio.create_task(self.probe.measure(profile))
        try:
            while not task.done():
                if not owns(await self.vpn.status(), candidate):
                    raise SafetyError(
                        "state_conflict",
                        "Tunnel changed during measurement; metrics discarded and scan paused.",
                    )
                await asyncio.wait({task}, timeout=self.poll_interval)
            metrics = await task
            if not owns(await self.vpn.status(), candidate):
                raise SafetyError(
                    "state_conflict", "Tunnel changed after probing; metrics discarded."
                )
            return metrics
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _attempt(self, candidate: Candidate, profile: Profile) -> Result:
        await self._disconnected()
        self.owned = None
        self.candidate_states[candidate.key] = "VPN connecting"
        self.emit(Progress("VPN connecting", message=candidate.key))
        phase = "preparation"
        timings: dict[str, float] = {}
        observations: list[str] = []
        connected_s = None
        phase_started = time.monotonic()

        def result(
            outcome: Outcome, code: str, detail: str, metrics: Metrics | None = None
        ) -> Result:
            timings.setdefault(phase, time.monotonic() - phase_started)
            observation = Result(
                candidate,
                outcome,
                code,
                detail,
                connected_s,
                metrics or Metrics(),
                context=profile.context,
                phase=phase,
                timings=timings,
                observations=observations,
            )
            self.candidate_states[candidate.key] = observation.state_label
            return observation

        try:
            prepare = getattr(self.vpn, "prepare", None)
            if prepare is not None:
                async with asyncio.timeout(90):
                    await prepare(candidate)
            timings[phase] = time.monotonic() - phase_started
            phase, phase_started = "dispatch", time.monotonic()
            async with asyncio.timeout(profile.dispatch_timeout):
                await self.vpn.connect(candidate)
            timings[phase] = time.monotonic() - phase_started
            # The establishment clock starts after IPC dispatch, not catalog discovery.
            phase, phase_started = "establishment", time.monotonic()
            deadline = phase_started + profile.timeout
            saw_connecting = False
            while True:
                # Finish each bounded poll. A slow poll is not a native connection timeout.
                try:
                    async with asyncio.timeout(profile.status_timeout):
                        status = await self.vpn.status()
                except TimeoutError:
                    return result(
                        Outcome.UNKNOWN,
                        "status_timeout",
                        "Status poll deadline expired; native state is unknown. Scan paused.",
                    )
                observation = status.state.value + (
                    ":" + status.diagnostic if status.diagnostic else ""
                )
                if not observations or observations[-1] != observation:
                    observations.append(observation)
                    observations[:] = observations[-32:]
                if status.state == State.CONNECTED:
                    if not owns(status, candidate):
                        raise SafetyError(
                            "state_conflict", "Connected endpoint does not match; scan paused."
                        )
                    connected_s = time.monotonic() - phase_started
                    self.owned = candidate
                    break
                if status.state == State.CONNECTING:
                    saw_connecting = True
                elif status.state == State.DISCONNECTED:
                    if status.diagnostic or saw_connecting:
                        return result(
                            Outcome.FAILED,
                            status.diagnostic or "connection_failed",
                            "Native CLI reported failure or returned from connecting to "
                            "disconnected. Cause unknown unless identified by its diagnostic.",
                        )
                else:
                    raise SafetyError("state_conflict", "Unexpected native state; scan paused.")
                if time.monotonic() >= deadline:
                    # Only a positively disconnected state permits another candidate.
                    return result(
                        Outcome.FAILED if status.state == State.DISCONNECTED else Outcome.UNKNOWN,
                        "connection_timeout",
                        "Application establishment deadline expired. "
                        "No transport cause is proven. Last observed state: " + status.state.value,
                    )
                await asyncio.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
            timings[phase] = time.monotonic() - phase_started
            phase, phase_started = "validation", time.monotonic()
            self.candidate_states[candidate.key] = "VPN connected but unverified"
            self.emit(Progress("VPN connected but unverified", message=candidate.key))
            await asyncio.sleep(profile.settle)
            if not owns(await self.vpn.status(), candidate):
                raise SafetyError("state_conflict", "Tunnel changed while settling; scan paused.")
            # Probe applies separate route-validation and HTTPS deadlines, then optional quality.
            metrics = await self._quality(candidate, profile)
            timings.update(metrics.timings)
            phase = "verification"
            return result(
                Outcome.SUCCESS if metrics.https_ok is True else Outcome.FAILED,
                "verified" if metrics.https_ok is True else "https_failed",
                "Native city/protocol/port and tunnel-bound HTTPS verified; nickname "
                "remains requested, not observable."
                if metrics.https_ok is True
                else "Native connected, but HTTPS verification failed. Cause is not established.",
                metrics,
            )
        except TimeoutError:
            return result(
                Outcome.UNKNOWN,
                phase + "_timeout",
                "Phase deadline expired; scan paused without disconnecting.",
            )
        except SafetyError:
            raise
        except AppError as exc:
            timings.update(exc.timings)
            if exc.code.startswith("https"):
                phase = "https"
            elif exc.code.startswith(("validation", "tunnel_")):
                phase = "validation"
            return result(Outcome.UNKNOWN, exc.code, str(exc))

    async def connect(
        self, candidate: Candidate, profile: Profile, interrupt: bool = False
    ) -> Result:
        self.gate.require_live()
        profile.validate()
        with self.lock.hold():
            self.task = asyncio.current_task()
            try:
                if interrupt:
                    await self._disconnect()
                result = await self._attempt(candidate, profile)
                self.candidate_states[candidate.key] = result.state_label
                self.store.record(result)
                return result
            except asyncio.CancelledError:
                self.candidate_states[candidate.key] = "cancelled"
                self.store.record(
                    Result(
                        candidate,
                        Outcome.CANCELLED,
                        "cancelled",
                        "Stopped; current system connection retained.",
                        context=profile.context,
                    )
                )
                raise
            finally:
                self.task = None

    async def scan(self, scan_id: int, catalog: Catalog) -> None:
        self.gate.require_live()
        with self.lock.hold():
            self.task = asyncio.current_task()
            try:
                profile = Profile.from_dict(json.loads(self.store.scan(scan_id)["profile"]))
                async with asyncio.timeout(profile.session_budget):
                    await self._scan(scan_id, catalog)
            except TimeoutError:
                self.store.set_state(scan_id, "paused")
                self.emit(
                    Progress("paused", message="Session budget reached; current VPN retained.")
                )
            except asyncio.CancelledError:
                self.store.set_state(scan_id, "paused")
                raise
            finally:
                self.task = None

    async def _scan(self, scan_id: int, catalog: Catalog) -> None:
        await self._disconnected()
        self.owned = None
        row = self.store.scan(scan_id)
        profile = Profile.from_dict(json.loads(row["profile"]))
        pending = self.store.pending(scan_id)
        # Resumes must still be valid in the latest official catalog/capabilities.
        available = {loc.key for loc in catalog.locations if loc.available}
        for _, candidate in pending:
            if (
                candidate.location.key not in available
                or candidate.port not in catalog.capabilities.get(candidate.protocol, [])
            ):
                raise SafetyError(
                    "catalog_changed",
                    "Queued endpoint is no longer available; "
                    "generate a new scan after refreshing the catalog.",
                )
        self.task = asyncio.current_task()
        self.store.set_state(scan_id, "running")
        attempts = 0  # Budget is per explicitly confirmed session, including resumes.
        session_started = time.monotonic()
        attempted: list[Candidate] = []
        durations: list[float] = []
        if self.preflight is not None:
            shortlist = await asyncio.to_thread(
                adaptive_order,
                [c for _, c in pending],
                profile,
                self.store.history(profile.context),
                limit=profile.preflight_budget,
            )
            self.emit(
                Progress(
                    "preflight",
                    total=len(shortlist),
                    message=f"Concurrency {profile.preflight_concurrency}; "
                    "TCP evidence is not VPN Ping. "
                    + getattr(self.preflight.inventory, "reason", ""),
                )
            )

            def observed(e: Evidence, n: int, total: int) -> None:
                self.evidence[e.candidate_key] = e
                self.store.record_preflight(scan_id, e)
                self.emit(Progress("preflight", n, total, e.code, evidence=e))

            self.evidence = await self.preflight.run(shortlist, profile, observed)
            for e in self.evidence.values():
                self.store.record_preflight(scan_id, e)
        active: tuple[int, Candidate] | None = None
        try:
            index = 0
            while pending:
                if (
                    attempts >= profile.max_tests
                    or time.monotonic() - session_started >= profile.session_budget
                ):
                    break
                candidate = adaptive_order(
                    [c for _, c in pending],
                    profile,
                    self.store.history(profile.context),
                    self.evidence,
                    attempted,
                    limit=1,
                )[0]
                position = next(pos for pos, c in pending if c == candidate)
                pending.remove((position, candidate))
                attempted.append(candidate)
                index += 1
                if self.owned:
                    await self._disconnect(self.owned)
                for retry in range(profile.retries + 1):
                    if attempts >= profile.max_tests:
                        break
                    active = (position, candidate)
                    effective = profile
                    history = [
                        r
                        for r in rank_results(self.store.history(profile.context))
                        if r.candidate.key == candidate.key and r.verified
                    ]
                    if profile.strategy == "turbo" and history and history[0].verified:
                        prior = history[0].verified.connect_s
                        if prior is not None:
                            effective = replace(
                                profile, timeout=min(profile.timeout, max(3, prior * 2 + 3))
                            )
                    attempt_started = time.monotonic()
                    result = await self._attempt(candidate, effective)
                    durations.append(time.monotonic() - attempt_started)
                    self.candidate_states[candidate.key] = result.state_label
                    attempts += 1
                    again = (
                        result.outcome == Outcome.FAILED
                        and retry < profile.retries
                        and attempts < profile.max_tests
                    )
                    self.store.record(
                        result,
                        scan_id,
                        position,
                        complete=not again and result.outcome != Outcome.UNKNOWN,
                    )
                    active = None
                    eta = (
                        (
                            (sum(durations) / len(durations) + profile.interval)
                            * min(len(pending), profile.max_tests - attempts)
                        )
                        if len(durations) >= 3
                        else None
                    )
                    self.emit(
                        Progress(
                            "VPN verified"
                            if result.outcome == Outcome.SUCCESS
                            else "timed out"
                            if "timeout" in result.code
                            else "failed",
                            index,
                            index + len(pending),
                            result.detail,
                            result,
                            eta_s=eta,
                        )
                    )
                    if result.outcome == Outcome.UNKNOWN:
                        self.store.set_state(scan_id, "paused")
                        return
                    if result.outcome == Outcome.SUCCESS and profile.mode != "rank":
                        self.store.set_state(scan_id, "succeeded")
                        return
                    if not again:
                        break
                    if self.owned:
                        await self._disconnect(self.owned)
                    await asyncio.sleep(profile.interval * (retry + 1))
                if pending and attempts < profile.max_tests:
                    await asyncio.sleep(profile.interval)
            self.store.set_state(scan_id, "limited" if self.store.pending(scan_id) else "complete")
        except asyncio.CancelledError:
            if active:
                self.candidate_states[active[1].key] = "cancelled"
                self.store.record(
                    Result(
                        active[1],
                        Outcome.CANCELLED,
                        "cancelled",
                        "Stopped; current system connection retained.",
                        context=profile.context,
                    ),
                    scan_id,
                    active[0],
                    complete=False,
                )
            self.store.set_state(scan_id, "paused")
            raise
        except AppError as exc:
            if active:
                self.store.record(
                    Result(active[1], Outcome.UNKNOWN, exc.code, str(exc), context=profile.context),
                    scan_id,
                    active[0],
                    complete=False,
                )
            self.store.set_state(scan_id, "paused")
            raise
        finally:
            self.task = None
            self.emit(Progress("idle", message="Stopped. Current VPN state retained."))
