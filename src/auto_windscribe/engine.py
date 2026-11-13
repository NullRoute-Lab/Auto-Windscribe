"""Sequential controller with ownership checks and interruption-safe persistence."""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from .errors import AppError, SafetyError
from .models import Candidate, Metrics, Outcome, Profile, Result, State, Status
from .probes import Probe
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
    ) -> None:
        self.vpn, self.probe, self.store = vpn, probe, store
        self.lock, self.gate = lock, gate
        self.poll_interval = poll_interval
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
        self.emit(Progress("connecting", message=candidate.key))
        started = time.monotonic()
        connected_s = None
        try:
            async with asyncio.timeout(profile.timeout):
                dispatched = await self.vpn.connect(candidate)
                started = dispatched if dispatched is not None else started
                saw_connecting = False
                while True:
                    status = await self.vpn.status()
                    if status.state == State.CONNECTED:
                        if not owns(status, candidate):
                            raise SafetyError(
                                "state_conflict",
                                "Connected endpoint/protocol/port "
                                "does not match the request. No success recorded.",
                            )
                        connected_s = time.monotonic() - started
                        self.owned = candidate
                        break
                    if status.state == State.CONNECTING:
                        saw_connecting = True
                    elif status.state == State.DISCONNECTED:
                        if status.diagnostic or saw_connecting or time.monotonic() - started >= 2:
                            return Result(
                                candidate,
                                Outcome.FAILED,
                                status.diagnostic or "connection_failed",
                                "Request was accepted but no tunnel was established. "
                                "Protocol filtering or server failure may be involved.",
                                context=profile.context,
                            )
                    else:
                        raise SafetyError(
                            "state_conflict", "Unexpected connection state; scan paused."
                        )
                    await asyncio.sleep(self.poll_interval)
            await asyncio.sleep(profile.settle)
            if not owns(await self.vpn.status(), candidate):
                raise SafetyError("state_conflict", "Tunnel changed while settling; scan paused.")
            self.emit(Progress("verifying", message=candidate.key))
            metrics = await self._quality(candidate, profile)
            success = metrics.https_ok is True
            return Result(
                candidate,
                Outcome.SUCCESS if success else Outcome.FAILED,
                "verified" if success else "https_failed",
                "CLI city/protocol/port and tunnel-bound HTTPS verified. "
                "Nickname is the requested endpoint; CLI may expose only the city."
                if success
                else "CLI connected, but tunnel-bound HTTPS did not succeed. "
                "This does not by itself identify the cause.",
                connected_s,
                metrics,
                context=profile.context,
            )
        except TimeoutError:
            return Result(
                candidate,
                Outcome.UNKNOWN,
                "connection_timeout",
                "Connection deadline expired. The system may still be connecting; "
                "no automatic cleanup or retry. Inspect status.",
                context=profile.context,
            )
        except SafetyError:
            raise
        except AppError as exc:
            return Result(
                candidate, Outcome.UNKNOWN, exc.code, str(exc), connected_s, context=profile.context
            )

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
                self.store.record(result)
                return result
            except asyncio.CancelledError:
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
                await self._scan(scan_id, catalog)
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
        attempts = row["attempts"]
        active: tuple[int, Candidate] | None = None
        try:
            for index, (position, candidate) in enumerate(pending):
                if attempts >= profile.max_tests:
                    break
                if self.owned:
                    await self._disconnect(self.owned)
                for retry in range(profile.retries + 1):
                    if attempts >= profile.max_tests:
                        break
                    active = (position, candidate)
                    result = await self._attempt(candidate, profile)
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
                    self.emit(Progress("result", index + 1, len(pending), result.detail, result))
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
                if index + 1 < len(pending) and attempts < profile.max_tests:
                    await asyncio.sleep(profile.interval)
            self.store.set_state(scan_id, "limited" if self.store.pending(scan_id) else "complete")
        except asyncio.CancelledError:
            if active:
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
