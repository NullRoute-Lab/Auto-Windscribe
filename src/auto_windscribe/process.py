"""Bounded argv subprocesses; terminate only the process this adapter spawned."""

import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import AppError


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    async def run(self, argv: Sequence[str], timeout: float = 10) -> Completed:  # noqa: ASYNC109
        ...


class AsyncRunner:
    async def run(self, argv: Sequence[str], timeout: float = 10) -> Completed:  # noqa: ASYNC109
        env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except OSError as exc:
            raise AppError(
                "executable_unavailable", "Required executable could not be started."
            ) from exc

        async def read_limited(stream: asyncio.StreamReader | None) -> bytes:
            assert stream is not None
            chunks = bytearray()
            while chunk := await stream.read(16384):
                chunks.extend(chunk)
                if len(chunks) > 1_048_576:
                    raise AppError("output_limit", "Command output exceeded the safety limit.")
            return bytes(chunks)

        async def collect() -> tuple[bytes, bytes]:
            out, err = await asyncio.gather(read_limited(proc.stdout), read_limited(proc.stderr))
            await proc.wait()
            return out, err

        task = asyncio.create_task(collect())
        try:
            out, err = await asyncio.wait_for(task, timeout)
            return Completed(
                proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
            )
        except (TimeoutError, asyncio.CancelledError, AppError) as exc:
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), 1)
                except TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    await proc.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if isinstance(exc, TimeoutError):
                raise AppError(
                    "command_timeout", "CLI command timed out; state needs review."
                ) from exc
            raise
