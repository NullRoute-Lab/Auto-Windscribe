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
        if version not in (0, 1, 2, 3, 4):
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
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY, scan_id INTEGER NOT NULL, started REAL NOT NULL,
                ended REAL, attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS in_flight (
                scan_id INTEGER, candidate_key TEXT, PRIMARY KEY(scan_id,candidate_key));
            CREATE TABLE IF NOT EXISTS dispositions (
                scan_id INTEGER, position INTEGER, status TEXT NOT NULL,
                updated REAL NOT NULL, PRIMARY KEY(scan_id,position));
            CREATE TABLE IF NOT EXISTS attempt_recovery (
                id INTEGER PRIMARY KEY, result_id INTEGER, scan_id INTEGER,
                candidate_key TEXT, created REAL NOT NULL, payload TEXT NOT NULL);
            PRAGMA user_version=4;
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

    def recovery_record(
        self, scan_id: int | None, report: Any, candidate: Candidate | None = None
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO recovery_events(scan_id,created,payload) VALUES(?,?,?)",
                (scan_id, time.time(), encode(report)),
            )
            if candidate is not None and scan_id is not None:
                row = self.conn.execute(
                    "SELECT max(id) FROM results WHERE scan_id=? AND candidate_key=?",
                    (scan_id, candidate.key),
                ).fetchone()
                self.conn.execute(
                    "INSERT INTO attempt_recovery(result_id,scan_id,candidate_key,created,payload) "
                    "VALUES(?,?,?,?,?)",
                    (row[0], scan_id, candidate.key, time.time(), encode(report)),
                )

    def recovery_status(self) -> dict:
        row = self.conn.execute(
            "SELECT payload FROM recovery_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return json.loads(row[0]) if row else {"phase": "not attempted"}

    def checkpoint(self, scan_id: int, position: int, complete: bool = True) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE queue SET done=? WHERE scan_id=? AND position=?",
                (int(complete), scan_id, position),
            )

    def previous(self, scan_id: int, candidate: Candidate) -> Result | None:
        disposition = self.conn.execute(
            "SELECT d.status FROM dispositions d JOIN queue q "
            "ON d.scan_id=q.scan_id AND d.position=q.position "
            "WHERE q.scan_id=? AND q.candidate=?",
            (scan_id, encode(candidate)),
        ).fetchone()
        if disposition and disposition[0] == "retry":
            return None
        if self.conn.execute(
            "SELECT 1 FROM in_flight WHERE scan_id=? AND candidate_key=?", (scan_id, candidate.key)
        ).fetchone():
            from .models import Outcome

            return Result(
                candidate,
                Outcome.UNKNOWN,
                "interrupted_dispatch",
                "Process ended before committing attempt outcome",
                phase="dispatch",
            )
        row = self.conn.execute(
            "SELECT payload FROM results WHERE scan_id=? AND candidate_key=? "
            "ORDER BY id DESC LIMIT 1",
            (scan_id, candidate.key),
        ).fetchone()
        return Result.from_dict(json.loads(row[0])) if row else None

    def start_session(self, scan_id: int) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO sessions(scan_id,started) VALUES(?,?)", (scan_id, time.time())
            )
            return int(cursor.lastrowid or 0)

    def end_session(self, session_id: int, attempts: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET ended=?,attempts=? WHERE id=?",
                (time.time(), attempts, session_id),
            )

    def manifest(self, scan_id: int) -> dict:
        row = self.scan(scan_id)
        counts = dict(
            self.conn.execute(
                "SELECT done,count(*) FROM queue WHERE scan_id=? GROUP BY done", (scan_id,)
            ).fetchall()
        )
        latest = self.conn.execute(
            "SELECT outcome,count(*) FROM results WHERE id IN "
            "(SELECT max(id) FROM results WHERE scan_id=? GROUP BY candidate_key) GROUP BY outcome",
            (scan_id,),
        ).fetchall()
        outcomes = dict(latest)
        sessions = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM sessions WHERE scan_id=? ORDER BY id", (scan_id,)
            )
        ]
        return {
            "scan_id": scan_id,
            "state": row["state"],
            "total": sum(counts.values()),
            "completed": counts.get(1, 0),
            "remaining": counts.get(0, 0),
            "skipped": 0,
            "tested": sum(outcomes.values()),
            "attempted": sum(outcomes.values()),
            "deferred": self.conn.execute(
                "SELECT count(*) FROM dispositions WHERE scan_id=? AND status='deferred'",
                (scan_id,),
            ).fetchone()[0],
            "verified": outcomes.get("success", 0),
            "failed": outcomes.get("failed", 0),
            "unknown": outcomes.get("unknown", 0),
            "cancelled": outcomes.get("cancelled", 0),
            "attempts": row["attempts"],
            "sessions": sessions,
            "profile": json.loads(row["profile"]),
        }

    def reserve(self, scan_id: int, candidate: Candidate) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM dispositions WHERE scan_id=? AND position IN "
                "(SELECT position FROM queue WHERE scan_id=? AND candidate=?)",
                (scan_id, scan_id, encode(candidate)),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO in_flight VALUES(?,?)", (scan_id, candidate.key)
            )

    def release(self, scan_id: int, candidate: Candidate) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM in_flight WHERE scan_id=? AND candidate_key=?",
                (scan_id, candidate.key),
            )

    def defer(self, scan_id: int, position: int) -> None:
        """Safely settled UNKNOWN is processed, never rewritten as FAILED."""
        with self.conn:
            self.conn.execute(
                "UPDATE queue SET done=1 WHERE scan_id=? AND position=?", (scan_id, position)
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO dispositions VALUES(?,?,?,?)",
                (scan_id, position, "deferred", time.time()),
            )

    def retry_deferred(self, scan_id: int) -> int:
        """Explicit retry selection only; history and original scan identity remain intact."""
        self.scan(scan_id)
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE queue SET done=0 WHERE scan_id=? AND position IN "
                "(SELECT position FROM dispositions WHERE scan_id=? AND status='deferred')",
                (scan_id, scan_id),
            )
            self.conn.execute(
                "UPDATE dispositions SET status='retry',updated=? WHERE scan_id=? "
                "AND status='deferred'",
                (time.time(), scan_id),
            )
            if cursor.rowcount:
                self.conn.execute("UPDATE scans SET state='pending' WHERE id=?", (scan_id,))
            return cursor.rowcount

    def has_unresolved_dispatch(self, except_scan: int | None = None) -> bool:
        if except_scan is None:
            return self.conn.execute("SELECT 1 FROM in_flight LIMIT 1").fetchone() is not None
        return (
            self.conn.execute(
                "SELECT 1 FROM in_flight WHERE scan_id != ? LIMIT 1", (except_scan,)
            ).fetchone()
            is not None
        )
