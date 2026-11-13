"""Private XDG storage, atomic config/cache updates and transactional scan history."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import AppError
from .models import Candidate, Location, Profile, Result, builtin_profiles, encode


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    cache: Path

    @classmethod
    def discover(cls) -> "Paths":
        def base(env: str, fallback: str) -> Path:
            value = os.environ.get(env, "")
            return Path(value) if value.startswith("/") else Path.home() / fallback

        return cls(
            base("XDG_CONFIG_HOME", ".config") / "auto-windscribe",
            base("XDG_DATA_HOME", ".local/share") / "auto-windscribe",
            base("XDG_CACHE_HOME", ".cache") / "auto-windscribe",
        )

    @classmethod
    def under(cls, root: Path) -> "Paths":
        return cls(root / "config", root / "data", root / "cache")

    def ensure(self) -> None:
        for path in (self.config, self.data, self.cache):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.chmod(0o700)


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2, ensure_ascii=False, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass
class Config:
    auto_scan: bool = True
    default_profile: str = "turbo"
    catalog_ttl: int = 86400

    @classmethod
    def load(cls, paths: Paths) -> "Config":
        path = paths.config / "config.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text())
            data.pop("live_enabled", None)  # v0.1 migration: never a permission prerequisite.
            conf = cls(**data)
            if type(conf.auto_scan) is not bool:
                raise ValueError("Consent flags must be boolean")
            if (
                type(conf.catalog_ttl) is not int
                or not 60 <= conf.catalog_ttl <= 604800
                or not isinstance(conf.default_profile, str)
            ):
                raise ValueError("Invalid app settings")
            return conf
        except (ValueError, TypeError, OSError) as exc:
            raise AppError(
                "config_invalid", "Invalid config.json; correct the application settings."
            ) from exc


class Profiles:
    def __init__(self, paths: Paths, readonly: bool = False) -> None:
        self.readonly = readonly
        self.path = paths.config / "profiles.json"

    def all(self) -> dict[str, Profile]:
        profiles = builtin_profiles()
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("Invalid profiles")
                for name, values in data.items():
                    profile = Profile.from_dict(values)
                    if profile.name != name:
                        raise ValueError("Profile key/name mismatch")
                    profiles[name] = profile
            except (ValueError, TypeError, OSError) as exc:
                raise AppError(
                    "profiles_invalid", "Invalid profiles.json; correct it before use."
                ) from exc
        return profiles

    def get(self, name: str) -> Profile:
        if name not in (profiles := self.all()):
            raise AppError("profile_missing", "Unknown profile. Use 'profiles' to list names.")
        return profiles[name]

    def save(self, profile: Profile) -> None:
        if self.readonly:
            raise AppError("offline", "Offline profiles cannot be changed.")
        profile.validate()
        profiles = self.all()
        profiles[profile.name] = profile
        atomic_json(self.path, {name: asdict(p) for name, p in profiles.items()})

    def delete(self, name: str) -> None:
        if self.readonly:
            raise AppError("offline", "Offline profiles cannot be changed.")
        profiles = self.all()
        profiles.pop(name, None)
        atomic_json(
            self.path,
            {
                n: asdict(p)
                for n, p in profiles.items()
                if n not in builtin_profiles() or p != builtin_profiles()[n]
            },
        )


@dataclass
class Catalog:
    locations: list[Location]
    capabilities: dict[str, list[int]]
    refreshed: float
    stale: bool = False
    warning: str = ""

    def save(self, path: Path) -> None:
        atomic_json(path, asdict(self))

    @classmethod
    def load(cls, path: Path, ttl: int = 86400) -> "Catalog":
        try:
            data = json.loads(path.read_text())
            locations = [Location(**loc) for loc in data["locations"]]
            caps = data["capabilities"]
            Profile(protocols=list(caps), ports=caps).validate()
            if not locations or not isinstance(data["refreshed"], (int, float)):
                raise ValueError("Empty catalog")
            for location in locations:
                for proto, ports in caps.items():
                    Candidate(location, proto, ports[0])
            return cls(locations, caps, data["refreshed"], time.time() - data["refreshed"] > ttl)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise AppError(
                "catalog_unavailable",
                "No valid cached catalog. Refresh locations "
                "through the official CLI; no static fallback is supplied.",
            ) from exc


class Store:
    def __init__(self, path: Path, readonly: bool = False) -> None:
        if readonly:
            self.conn = sqlite3.connect(":memory:")
            if path.exists():
                # mode=ro alone can update WAL shared-memory read marks. Snapshot only
                # our app DB/WAL into an ephemeral directory before SQLite opens them.
                with tempfile.TemporaryDirectory(prefix="auto-windscribe-read-") as directory:
                    target = Path(directory) / "history.sqlite3"
                    sources = [path, Path(str(path) + "-wal")]
                    for _attempt in range(3):
                        before = [
                            (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                            for p in sources
                        ]
                        for source, stamp in zip(sources, before, strict=True):
                            dest = target if source == path else Path(str(target) + "-wal")
                            if stamp is not None:
                                if stamp[0] > 512 * 1024 * 1024:
                                    raise AppError(
                                        "history_large", "Offline snapshot exceeds 512 MiB."
                                    )
                                shutil.copyfile(source, dest)
                            else:
                                dest.unlink(missing_ok=True)
                        after = [
                            (p.stat().st_size, p.stat().st_mtime_ns) if p.exists() else None
                            for p in sources
                        ]
                        if before == after:
                            break
                    else:
                        raise AppError(
                            "history_busy", "History changed while taking offline snapshot."
                        )
                    source_db = sqlite3.connect(target)
                    try:
                        source_db.backup(self.conn)
                    finally:
                        source_db.close()
            self._schema()
            return
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        self.conn = sqlite3.connect(path, timeout=5)
        self._schema()

    def _schema(self) -> None:
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            self.conn.close()
            raise AppError("schema_newer", "Database was created by a newer application.")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY, created REAL NOT NULL, profile TEXT NOT NULL,
                context TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS queue (
                scan_id INTEGER NOT NULL REFERENCES scans(id), position INTEGER NOT NULL,
                candidate TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scan_id, position)
            );
            CREATE TABLE IF NOT EXISTS results (
                id INTEGER PRIMARY KEY, scan_id INTEGER REFERENCES scans(id),
                candidate_key TEXT NOT NULL, context TEXT NOT NULL,
                created REAL NOT NULL, outcome TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS results_context ON results(context, candidate_key, created);
            CREATE TABLE IF NOT EXISTS preflight (
                scan_id INTEGER NOT NULL REFERENCES scans(id), candidate_key TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(scan_id, candidate_key)
            );
            CREATE TABLE IF NOT EXISTS recovery_events (
                id INTEGER PRIMARY KEY, scan_id INTEGER, created REAL, payload TEXT);
            PRAGMA user_version=2;
        """)

    def close(self) -> None:
        self.conn.close()

    def new_scan(self, queue: list[Candidate], profile: Profile) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO scans(created,profile,context,state) VALUES(?,?,?,?)",
                (time.time(), encode(profile), profile.context, "pending"),
            )
            scan_id = int(cursor.lastrowid or 0)
            self.conn.executemany(
                "INSERT INTO queue VALUES(?,?,?,0)",
                [(scan_id, pos, encode(c)) for pos, c in enumerate(queue)],
            )
        return scan_id

    def scan(self, scan_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM scans WHERE id=?", (scan_id,)).fetchone()
        if row is None:
            raise AppError("scan_missing", "Scan ID does not exist.")
        return row

    def pending(self, scan_id: int) -> list[tuple[int, Candidate]]:
        return [
            (row["position"], Candidate.from_dict(json.loads(row["candidate"])))
            for row in self.conn.execute(
                "SELECT * FROM queue WHERE scan_id=? AND done=0 ORDER BY position", (scan_id,)
            )
        ]

    def set_state(self, scan_id: int, state: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE scans SET state=? WHERE id=?", (state, scan_id))

    def record(
        self,
        result: Result,
        scan_id: int | None = None,
        position: int | None = None,
        complete: bool = True,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO results(scan_id,candidate_key,context,created,"
                "outcome,payload) VALUES(?,?,?,?,?,?)",
                (
                    scan_id,
                    result.candidate.key,
                    result.context,
                    result.timestamp,
                    result.outcome.value,
                    encode(result),
                ),
            )
            if scan_id is not None:
                self.conn.execute("UPDATE scans SET attempts=attempts+1 WHERE id=?", (scan_id,))
                if complete:
                    self.conn.execute(
                        "UPDATE queue SET done=1 WHERE scan_id=? AND position=?",
                        (scan_id, position),
                    )

    def history(self, context: str | None = None, limit: int = 10000) -> list[Result]:
        sql = "SELECT payload FROM results"
        params: list[Any] = []
        if context is not None:
            sql += " WHERE context=?"
            params.append(context)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [Result.from_dict(json.loads(row[0])) for row in self.conn.execute(sql, params)]

    def scans(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                "SELECT id,created,state,attempts FROM scans ORDER BY id DESC LIMIT 30"
            )
        ]

    def record_preflight(self, scan_id: int, evidence: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO preflight VALUES(?,?,?)",
                (scan_id, evidence.candidate_key, encode(evidence)),
            )

    def preflights(self, scan_id: int) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.conn.execute(
                "SELECT payload FROM preflight WHERE scan_id=?", (scan_id,)
            )
        ]

    def recovery_record(self, scan_id: int, report: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO recovery_events(scan_id,created,payload) VALUES(?,?,?)",
                (scan_id, time.time(), encode(report)),
            )

    def recovery_status(self) -> dict:
        row = self.conn.execute(
            "SELECT payload FROM recovery_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row[0]) if row else {"phase": "not attempted"}
