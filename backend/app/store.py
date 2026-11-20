"""SQLite store for users, RSVP runs, their steps and the latest frame. Owned by Part 5.

One connection guarded by a lock, so the API and the worker threads can share it.
DATABASE_PATH (default ./event_agent.db); frames are PNG files in `frames/` next to the db.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from app.contracts import (STEP_ORDER, STEP_TEXT, Arm, RsvpResult, RsvpRun, RsvpStep, RunNeed,
                           RunStatus, Site, StepState, User)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    verified INTEGER NOT NULL DEFAULT 0,
    persona_inquiry_id TEXT,
    into_json TEXT NOT NULL DEFAULT '[]',
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stop_reason TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    outcome TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    model TEXT,
    site TEXT NOT NULL DEFAULT 'luma',
    arm TEXT,
    need_json TEXT
);
CREATE INDEX IF NOT EXISTS runs_user ON runs (user_id, started_at);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    state TEXT NOT NULL,
    ord INTEGER NOT NULL,
    PRIMARY KEY (run_id, key)
);
CREATE TABLE IF NOT EXISTS frames (
    run_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

ACTIVE: tuple[RunStatus, ...] = ("queued", "running")

# Columns added after the first schema; added in place to an older db file.
_ADDED_COLUMNS = {
    "users": {"data_json": "TEXT NOT NULL DEFAULT '{}'"},
    "runs": {"outcome": "TEXT", "cost_usd": "REAL NOT NULL DEFAULT 0", "model": "TEXT",
             "site": "TEXT NOT NULL DEFAULT 'luma'", "arm": "TEXT", "need_json": "TEXT"},
}
LOCAL_TZ = ZoneInfo("America/Los_Angeles")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


class Store:
    def __init__(self, path: Optional[str] = None, frames_dir: Optional[str] = None) -> None:
        self.path = path or os.getenv("DATABASE_PATH", "./event_agent.db")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        base = Path(self.path).parent if self.path != ":memory:" else Path(".")
        self.frames_dir = Path(frames_dir or os.getenv("FRAMES_DIR") or base / "frames")
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            for table, cols in _ADDED_COLUMNS.items():
                have = {r["name"] for r in self._db.execute(f"PRAGMA table_info({table})")}
                for col, decl in cols.items():
                    if col not in have:
                        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            self._db.commit()

    # ------------------------------------------------------------ users

    def _user(self, row: sqlite3.Row) -> User:
        data = json.loads(row["data_json"] or "{}")
        data.update(id=row["id"], name=row["name"], verified=bool(row["verified"]),
                    persona_inquiry_id=row["persona_inquiry_id"], into=json.loads(row["into_json"]))
        return User.model_validate(data)

    @staticmethod
    def _user_params(user: User) -> tuple:
        return (user.id, user.name, int(user.verified), user.persona_inquiry_id, json.dumps(user.into),
                user.model_dump_json())

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            row = self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user(row) if row else None

    def ensure_user(self, user: User) -> User:
        """Create the user if new; return the stored copy either way."""
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO users (id, name, verified, persona_inquiry_id, into_json, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?)", self._user_params(user))
            self._db.commit()
            return self.get_user(user.id)  # type: ignore[return-value]

    def save_user(self, user: User) -> User:
        with self._lock:
            self._db.execute(
                "INSERT INTO users (id, name, verified, persona_inquiry_id, into_json, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                "verified = excluded.verified, persona_inquiry_id = excluded.persona_inquiry_id, "
                "into_json = excluded.into_json, data_json = excluded.data_json",
                self._user_params(user))
            self._db.commit()
        return user

    def set_verified(self, user_id: str, verified: bool, inquiry_id: Optional[str] = None,
                     verified_at: Optional[datetime] = None) -> Optional[User]:
        with self._lock:
            user = self.get_user(user_id)
            if not user:
                return None
            if verified and not user.verified:
                user.verified_at = verified_at or _now()
            user.verified = verified
            if inquiry_id:
                user.persona_inquiry_id = inquiry_id
            return self.save_user(user)

    def delete_user(self, user_id: str) -> None:
        """Removes the user, their runs, steps and frames."""
        with self._lock:
            run_ids = [r["id"] for r in self._db.execute("SELECT id FROM runs WHERE user_id = ?", (user_id,))]
            for run_id in run_ids:
                path = self.frame_path(run_id)
                if path:
                    path.unlink(missing_ok=True)
                self._db.execute("DELETE FROM steps WHERE run_id = ?", (run_id,))
                self._db.execute("DELETE FROM frames WHERE run_id = ?", (run_id,))
            self._db.execute("DELETE FROM runs WHERE user_id = ?", (user_id,))
            self._db.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self._db.commit()

    def set_into(self, user_id: str, into: list[str]) -> Optional[User]:
        with self._lock:
            user = self.get_user(user_id)
            if not user:
                return None
            user.into = list(into)
            return self.save_user(user)

    # ------------------------------------------------------------ runs

    def create_run(self, user_id: str, event_id: str, site: Site = "luma",
                   arm: Optional[Arm] = None) -> RsvpRun:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        started = _now().isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, user_id, event_id, status, started_at, site, arm) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?)", (run_id, user_id, event_id, started, site, arm))
            self._db.executemany(
                "INSERT INTO steps (run_id, key, title, detail, state, ord) VALUES (?, ?, ?, ?, 'waiting', ?)",
                [(run_id, key, *STEP_TEXT[key], i) for i, key in enumerate(STEP_ORDER)])
            self._db.commit()
            return self.get_run(run_id)  # type: ignore[return-value]

    def _run(self, row: sqlite3.Row) -> RsvpRun:
        steps = self._db.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY ord", (row["id"],)).fetchall()
        frame = self._db.execute("SELECT path FROM frames WHERE run_id = ?", (row["id"],)).fetchone()
        return RsvpRun(
            id=row["id"], user_id=row["user_id"], event_id=row["event_id"], status=row["status"],
            stop_reason=row["stop_reason"], started_at=_dt(row["started_at"]), ended_at=_dt(row["ended_at"]),
            steps=[RsvpStep(run_id=s["run_id"], key=s["key"], title=s["title"], detail=s["detail"],
                            state=s["state"]) for s in steps],
            has_frame=bool(frame and Path(frame["path"]).exists()),
            need=RunNeed.model_validate_json(row["need_json"]) if row["need_json"] else None,
            outcome=row["outcome"], cost_usd=row["cost_usd"] or 0.0, model=row["model"],
            site=row["site"] or "luma", arm=row["arm"])

    def get_run(self, run_id: str) -> Optional[RsvpRun]:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            return self._run(row) if row else None

    def list_runs(self, user_id: Optional[str] = None, since: Optional[datetime] = None) -> list[RsvpRun]:
        """Runs with their steps, newest first; optionally one user's, started at or after `since`."""
        where, args = [], []
        if user_id:
            where.append("user_id = ?")
            args.append(user_id)
        if since:
            since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)
            where.append("started_at >= ?")
            args.append(since.astimezone(timezone.utc).isoformat())
        sql = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "")
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY started_at DESC", args).fetchall()
            return [self._run(r) for r in rows]

    def active_run(self, user_id: str, event_id: str) -> Optional[RsvpRun]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM runs WHERE user_id = ? AND event_id = ? AND status IN ('queued', 'running') "
                "ORDER BY started_at DESC LIMIT 1", (user_id, event_id)).fetchone()
            return self._run(row) if row else None

    def spent_today(self, user_id: str) -> float:
        """Agent spend (USD) on runs started today, Pacific time."""
        start = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(cost_usd), 0) AS s FROM runs "
                                   "WHERE user_id = ? AND started_at >= ?",
                                   (user_id, start.astimezone(timezone.utc).isoformat())).fetchone()
        return float(row["s"])

    def set_status(self, run_id: str, status: RunStatus, stop_reason: Optional[str] = None) -> None:
        ended = None if status in ACTIVE else _now().isoformat()
        with self._lock:
            self._db.execute("UPDATE runs SET status = ?, stop_reason = ?, ended_at = ? WHERE id = ?",
                             (status, stop_reason, ended, run_id))
            self._db.commit()

    def set_need(self, run_id: str, need: Optional[RunNeed]) -> None:
        """Hand the run to the person (needs_you + what they must do), or take it back."""
        with self._lock:
            self._db.execute("UPDATE runs SET status = ?, need_json = ? WHERE id = ?",
                             ("needs_you" if need else "running", need.model_dump_json() if need else None,
                              run_id))
            self._db.commit()

    def finish_run(self, run_id: str, result: RsvpResult) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status = ?, stop_reason = ?, ended_at = ?, outcome = ?, cost_usd = ?, "
                "model = ?, need_json = ? WHERE id = ?",
                (result.status, result.stop_reason, _now().isoformat(), result.outcome, result.cost_usd,
                 result.model, result.need.model_dump_json() if result.need else None, run_id))
            self._db.commit()

    def save_step(self, step: RsvpStep) -> None:
        ord_ = STEP_ORDER.index(step.key)
        with self._lock:
            self._db.execute(
                "INSERT INTO steps (run_id, key, title, detail, state, ord) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, key) DO UPDATE SET title = excluded.title, detail = excluded.detail, "
                "state = excluded.state",
                (step.run_id, step.key, step.title, step.detail, step.state, ord_))
            self._db.commit()

    def mark_steps(self, run_id: str, from_state: StepState, to_state: StepState) -> None:
        with self._lock:
            self._db.execute("UPDATE steps SET state = ? WHERE run_id = ? AND state = ?",
                             (to_state, run_id, from_state))
            self._db.commit()

    def fail_unfinished(self, reason: str) -> int:
        """On startup: runs left queued/running by a previous process can't resume."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute(
                "SELECT id FROM runs WHERE status IN ('queued', 'running') "
                "OR (status = 'needs_you' AND ended_at IS NULL)").fetchall()]
            for run_id in ids:
                self.mark_steps(run_id, "current", "failed")
                self.set_status(run_id, "failed", reason)
            return len(ids)

    # ------------------------------------------------------------ frames

    def save_frame(self, run_id: str, png: bytes) -> Path:
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        path = self.frames_dir / f"{run_id}.png"
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_bytes(png)
        os.replace(tmp, path)  # atomic, so a reader never sees half a PNG
        with self._lock:
            self._db.execute(
                "INSERT INTO frames (run_id, path, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET path = excluded.path, updated_at = excluded.updated_at",
                (run_id, str(path.resolve()), _now().isoformat()))
            self._db.commit()
        return path

    def frame_path(self, run_id: str) -> Optional[Path]:
        with self._lock:
            row = self._db.execute("SELECT path FROM frames WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        path = Path(row["path"])
        return path if path.exists() else None

    def close(self) -> None:
        with self._lock:
            self._db.close()


@lru_cache
def get_store() -> Store:
    return Store()


# Module-level shortcuts, e.g. `from app import store; store.get_run(run_id)`.


def get_user(user_id: str) -> Optional[User]:
    return get_store().get_user(user_id)


def save_user(user: User) -> User:
    return get_store().save_user(user)


def list_runs(user_id: Optional[str] = None, since: Optional[datetime] = None) -> list[RsvpRun]:
    return get_store().list_runs(user_id, since)


def get_run(run_id: str) -> Optional[RsvpRun]:
    return get_store().get_run(run_id)
