"""Defense in depth: explicit live consent, offline override, process exclusion."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .errors import SafetyError


@dataclass(frozen=True)
class SafetyGate:
    enabled: bool = False
    confirmed: bool = False
    offline: bool = False

    def require_live(self) -> None:
        if self.offline or os.environ.get("AUTO_WINDSCRIBE_OFFLINE") == "1":
            raise SafetyError("offline_guard", "Offline mode forbids VPN operations and probes.")
        if not self.enabled or not self.confirmed:
            raise SafetyError(
                "authorization_required",
                "Live operations are locked. Enable live "
                "access in app config only after authorization, then explicitly "
                "confirm the action (--live in the CLI).",
            )


class OperationLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.busy = False

    @contextmanager
    def hold(self) -> Iterator[None]:
        if self.busy:
            raise SafetyError("busy", "A connection operation is already running.")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SafetyError(
                    "busy", "Another Auto-Windscribe process owns the tunnel."
                ) from exc
            self.busy = True
            try:
                yield
            finally:
                self.busy = False
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
