"""Narrow adapter around the official CLI. No settings files, firewall or helpers."""

import asyncio
import os
import re
import time
from pathlib import Path
from typing import Protocol

from .errors import AppError, SafetyError
from .models import Candidate, Location, State, Status
from .parsing import parse_locations, parse_ports, parse_protocols, parse_status
from .process import Runner
from .safety import SafetyGate


class VPN(Protocol):
    async def status(self) -> Status: ...
    async def locations(self) -> list[Location]: ...
    async def capabilities(self) -> dict[str, list[int]]: ...
    async def connect(self, candidate: Candidate) -> float | None: ...
    async def disconnect(self) -> None: ...


def discover_cli() -> str:
    path = Path("/usr/bin/windscribe-cli")
    if not path.is_file() or not os.access(path, os.X_OK):
        raise AppError("cli_missing", "Expected the existing /usr/bin/windscribe-cli executable.")
    return str(path)


class WindscribeCLI:
    def __init__(self, runner: Runner, gate: SafetyGate, path: str | None = None) -> None:
        self.runner, self.gate = runner, gate
        self.path = path or discover_cli()
        self._command_lock = asyncio.Lock()

    async def _read(self, *args: str) -> str:
        async with self._command_lock:
            result = await self.runner.run([self.path, *args], timeout=10)
        if result.returncode != 0:
            raise AppError(
                "cli_error",
                f"Windscribe {args[0]} failed (exit {result.returncode}). "
                "Check the official app; raw output is not logged.",
            )
        return result.stdout

    async def version(self) -> str:
        output = await self._read("version")
        match = re.search(r"\b\d+\.\d+\.\d+\b", output)
        if not match:
            raise AppError("version_unknown", "Cannot identify the native CLI version.")
        return match[0]

    async def status(self) -> Status:
        return parse_status(await self._read("status"))

    async def locations(self) -> list[Location]:
        return parse_locations(await self._read("locations"))

    async def capabilities(self) -> dict[str, list[int]]:
        protocols = parse_protocols(await self._read("help", "connect"))
        return {p: parse_ports(await self._read("ports", p)) for p in protocols}

    async def presets(self) -> list[str]:
        output = await self._read("amneziawg")
        return [
            line.strip()
            for line in output.splitlines()
            if re.fullmatch(r"[A-Z] - [\w .()-]+", line.strip())
        ]

    async def connect(self, candidate: Candidate) -> float | None:
        self.gate.require_live()
        if candidate.port not in (await self.capabilities()).get(candidate.protocol, []):
            raise SafetyError("unsupported_port", "Port no longer supported; fallback refused.")
        locations = await self.locations()
        same_name = [
            loc
            for loc in locations
            if loc.nickname.casefold() == candidate.location.nickname.casefold()
        ]
        if (
            len(same_name) != 1
            or not same_name[0].available
            or same_name[0].key != candidate.location.key
        ):
            raise SafetyError("catalog_changed", "Requested nickname is unavailable or ambiguous.")
        if (await self.status()).state != State.DISCONNECTED:
            raise SafetyError(
                "state_conflict", "Connection changed before request; connect refused."
            )
        # -n only dispatches the IPC request. The controller verifies status independently.
        dispatched = time.monotonic()
        await self._read(
            "connect", "-n", candidate.location.target, f"{candidate.protocol}:{candidate.port}"
        )

        return dispatched

    async def disconnect(self) -> None:
        self.gate.require_live()
        await self._read("disconnect")
