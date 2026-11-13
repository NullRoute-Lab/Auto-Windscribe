"""Bounded transport evidence, deliberately separate from VPN verification.

An Inventory adapter must establish address provenance and protocol mapping.
NativeInventory fails closed: the CLI cannot export addresses. No user-supplied
address switch, credential access, or nickname-to-hostname guessing exists.
"""

import asyncio
import ipaddress
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from .models import Candidate, Profile
from .safety import SafetyGate


class PreflightState(StrEnum):
    UNTESTED = "untested"
    REACHABLE = "preflight reachable"
    FAILED = "preflight failed"
    INCONCLUSIVE = "preflight inconclusive"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Endpoint:
    # Only constructed by a trusted inventory adapter, never from search text.
    host: str
    port: int
    source: str
    expires: float


@dataclass
class Evidence:
    candidate_key: str
    state: PreflightState = PreflightState.INCONCLUSIVE
    code: str = "endpoint_unavailable"
    tcp_ms: float | None = None
    attempts: int = 0
    timestamp: float = field(default_factory=time.time)
    # No endpoint addresses or raw exceptions in stored observations.


class Inventory(Protocol):
    async def endpoints(self, candidates: list[Candidate]) -> dict[str, Endpoint]: ...


class NativeInventory:
    reason = (
        "Native CLI has no endpoint-address export. Official protocol-to-IP mapping "
        "requires authenticated PortMap data. Preflight skipped; no addresses guessed."
    )

    async def endpoints(self, candidates: list[Candidate]) -> dict[str, Endpoint]:
        return {}


class Transport(Protocol):
    async def tcp(self, endpoint: Endpoint, deadline: float) -> float: ...


class TCPTransport:
    """Cancellable TCP connect; no payload, TLS, UDP, or tunnel attempts."""

    def __init__(self, gate: SafetyGate, dns_ttl: float = 60) -> None:
        self.gate = gate
        self.dns_ttl = dns_ttl
        self.cache: dict[str, tuple[float, list[tuple[int, str]]]] = {}
        self._dns_locks: dict[str, asyncio.Lock] = {}

    async def _resolve(self, host: str) -> list[tuple[int, str]]:
        async with self._dns_locks.setdefault(host, asyncio.Lock()):
            cached = self.cache.get(host)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            addresses: list[tuple[int, str]]
            try:
                address = ipaddress.ip_address(host)
                addresses = [
                    (socket.AF_INET if address.version == 4 else socket.AF_INET6, str(address))
                ]
            except ValueError:
                records = await asyncio.get_running_loop().getaddrinfo(
                    host, None, type=socket.SOCK_STREAM
                )
                addresses = list(dict.fromkeys((int(r[0]), str(r[4][0])) for r in records))[:4]
            if not addresses or any(not ipaddress.ip_address(a).is_global for _, a in addresses):
                raise ValueError("Non-public endpoint refused")
            if len(self.cache) >= 2048:
                self.cache.clear()
            self.cache[host] = (time.monotonic() + self.dns_ttl, addresses)
            return addresses

    async def tcp(self, endpoint: Endpoint, deadline: float) -> float:
        self.gate.require_live()
        if (
            endpoint.expires <= time.time()
            or not endpoint.source
            or not 1 <= endpoint.port <= 65535
        ):
            raise ValueError("Missing or stale trusted endpoint metadata")
        async with asyncio.timeout_at(deadline):
            addresses = await self._resolve(endpoint.host)
            last_error: OSError | None = None
            for family, address in addresses:
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.setblocking(False)
                started = time.monotonic()
                try:
                    await asyncio.get_running_loop().sock_connect(sock, (address, endpoint.port))
                    return (time.monotonic() - started) * 1000
                except OSError as exc:
                    last_error = exc
                finally:
                    sock.close()
            raise last_error or OSError("No addresses")


class Preflight:
    def __init__(self, inventory: Inventory, transport: Transport, gate: SafetyGate) -> None:
        self.inventory, self.transport, self.gate = inventory, transport, gate
        self.used = 0
        self.peak = 0

    async def run(
        self,
        candidates: list[Candidate],
        profile: Profile,
        emit: Callable[[Evidence, int, int], None] = lambda e, n, total: None,
    ) -> dict[str, Evidence]:
        self.gate.require_live()
        profile.validate()
        self.used = self.peak = 0
        batch_deadline = asyncio.get_running_loop().time() + profile.preflight_deadline
        evidence = {
            c.key: Evidence(
                c.key,
                code="udp_uncertain"
                if c.protocol in ("udp", "wireguard")
                else "endpoint_unavailable",
            )
            for c in candidates
        }
        try:
            async with asyncio.timeout_at(batch_deadline):
                endpoints = await self.inventory.endpoints(candidates)
        except (TimeoutError, OSError, ValueError):
            for result in evidence.values():
                result.code = "inventory_unavailable"
            return evidence
        groups: dict[tuple[str, int], tuple[Endpoint, list[str]]] = {}
        for candidate in candidates:
            endpoint = endpoints.get(candidate.key)
            if candidate.protocol in ("udp", "wireguard") or endpoint is None:
                continue
            if (
                endpoint.port != candidate.port
                or endpoint.expires <= time.time()
                or not endpoint.source
            ):
                evidence[candidate.key].code = "metadata_untrusted"
                continue
            groups.setdefault((endpoint.host, endpoint.port), (endpoint, []))[1].append(
                candidate.key
            )
        queue: asyncio.Queue[tuple[Endpoint, list[str]]] = asyncio.Queue()
        for group in groups.values():
            queue.put_nowait(group)
        active = completed = 0
        total = len(groups)
        rate_lock = asyncio.Lock()
        next_slot = time.monotonic()

        async def worker() -> None:
            nonlocal active, completed, next_slot
            while not queue.empty():
                endpoint, keys = queue.get_nowait()
                result = Evidence(keys[0], code="budget_exhausted")
                try:
                    for retry in range(profile.preflight_retries + 1):
                        async with rate_lock:
                            if self.used >= profile.preflight_budget:
                                break
                            await asyncio.sleep(max(0, next_slot - time.monotonic()))
                            next_slot = time.monotonic() + 1 / profile.preflight_rate
                            self.used += 1
                        result.attempts += 1
                        active += 1
                        self.peak = max(self.peak, active)
                        try:
                            deadline = asyncio.get_running_loop().time() + profile.preflight_timeout
                            async with asyncio.timeout_at(deadline):
                                result.tcp_ms = await self.transport.tcp(endpoint, deadline)
                            result.state, result.code = PreflightState.REACHABLE, "tcp_only"
                            break
                        except TimeoutError:
                            result.state, result.code = PreflightState.FAILED, "tcp_timeout"
                        except socket.gaierror:
                            result.state, result.code = PreflightState.FAILED, "dns_failed"
                        except ConnectionRefusedError:
                            result.state, result.code = PreflightState.FAILED, "tcp_refused"
                            break
                        except OSError:
                            result.state, result.code = PreflightState.FAILED, "transport_error"
                        except ValueError:
                            result.state, result.code = (
                                PreflightState.INCONCLUSIVE,
                                "metadata_untrusted",
                            )
                            break
                        finally:
                            active -= 1
                        if retry < profile.preflight_retries:
                            await asyncio.sleep(min(0.1 * (retry + 1), profile.preflight_timeout))
                except asyncio.CancelledError:
                    result.state, result.code = PreflightState.CANCELLED, "cancelled"
                    raise
                finally:
                    completed += 1
                    for key in keys:
                        copy = Evidence(
                            key,
                            result.state,
                            result.code,
                            result.tcp_ms,
                            result.attempts,
                            result.timestamp,
                        )
                        evidence[key] = copy
                        emit(copy, completed, total)
                    queue.task_done()

        tasks = [
            asyncio.create_task(worker()) for _ in range(min(profile.preflight_concurrency, total))
        ]
        try:
            async with asyncio.timeout_at(batch_deadline):
                await asyncio.gather(*tasks)
        except TimeoutError:
            for result in evidence.values():
                if result.code == "endpoint_unavailable":
                    result.code = "preflight_deadline"
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return evidence
