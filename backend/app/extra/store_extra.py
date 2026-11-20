"""Part 8's own SQLite tables, in the same file as the main store (DATABASE_PATH).

Sign-in codes (hashed, 10-minute expiry), which event sites and calendar each person connected,
the usual-times fact id (so a new answer replaces the old one), and a small counter of For you
picks shown per ranking arm (the Ops dashboard's denominator).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

CODE_MINUTES = 10
MAX_ATTEMPTS = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extra_auth_codes (
    email TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS extra_email_users (
    email TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extra_sites (
    user_id TEXT NOT NULL,
    site TEXT NOT NULL,
    connected INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, site)
);
CREATE TABLE IF NOT EXISTS extra_calendar (
    user_id TEXT PRIMARY KEY,
    connected INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS extra_prefs (
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS extra_asks (
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    user_id TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    PRIMARY KEY (run_id, kind)
);
CREATE TABLE IF NOT EXISTS extra_pick_views (
    day TEXT NOT NULL,
    arm TEXT NOT NULL,
    shown INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, arm)
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(code: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()


class ExtraStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.getenv("DATABASE_PATH", "./event_agent.db")
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ------------------------------------------------------------ sign-in codes

    def new_code(self, email: str) -> str:
        """Makes a fresh 6-digit code for this email (replacing any older one)."""
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(8)
        expires = (_now() + timedelta(minutes=CODE_MINUTES)).isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO extra_auth_codes (email, code_hash, salt, expires_at, attempts) "
                "VALUES (?, ?, ?, ?, 0) ON CONFLICT(email) DO UPDATE SET code_hash = excluded.code_hash, "
                "salt = excluded.salt, expires_at = excluded.expires_at, attempts = 0",
                (email, _hash(code, salt), salt, expires))
            self._db.commit()
        return code

    def check_code(self, email: str, code: str) -> Optional[str]:
        """None when the code is right (and used up); else why it isn't."""
        with self._lock:
            row = self._db.execute("SELECT * FROM extra_auth_codes WHERE email = ?", (email,)).fetchone()
            if not row:
                return "Ask for a code first."
            if datetime.fromisoformat(row["expires_at"]) < _now():
                self._db.execute("DELETE FROM extra_auth_codes WHERE email = ?", (email,))
                self._db.commit()
                return "That code expired. Ask for a new one."
            if row["attempts"] >= MAX_ATTEMPTS:
                return "Too many tries. Ask for a new code."
            if not hmac.compare_digest(_hash(code.strip(), row["salt"]), row["code_hash"]):
                self._db.execute("UPDATE extra_auth_codes SET attempts = attempts + 1 WHERE email = ?", (email,))
                self._db.commit()
                return "That code isn't right."
            self._db.execute("DELETE FROM extra_auth_codes WHERE email = ?", (email,))
            self._db.commit()
            return None

    def user_for_email(self, email: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT user_id FROM extra_email_users WHERE email = ?", (email,)).fetchone()
        return row["user_id"] if row else None

    def link_email(self, email: str, user_id: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO extra_email_users (email, user_id) VALUES (?, ?) "
                             "ON CONFLICT(email) DO UPDATE SET user_id = excluded.user_id", (email, user_id))
            self._db.commit()

    # ------------------------------------------------------------ sites and calendar

    def sites(self, user_id: str) -> dict[str, bool]:
        with self._lock:
            rows = self._db.execute("SELECT site, connected FROM extra_sites WHERE user_id = ?",
                                    (user_id,)).fetchall()
        return {r["site"]: bool(r["connected"]) for r in rows}

    def set_site(self, user_id: str, site: str, connected: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO extra_sites (user_id, site, connected, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, site) DO UPDATE SET connected = excluded.connected, "
                "updated_at = excluded.updated_at", (user_id, site, int(connected), _now().isoformat()))
            self._db.commit()

    def calendar(self, user_id: str) -> Optional[bool]:
        """None when the person never chose."""
        with self._lock:
            row = self._db.execute("SELECT connected FROM extra_calendar WHERE user_id = ?", (user_id,)).fetchone()
        return bool(row["connected"]) if row else None

    def set_calendar(self, user_id: str, connected: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO extra_calendar (user_id, connected, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET connected = excluded.connected, updated_at = excluded.updated_at",
                (user_id, int(connected), _now().isoformat()))
            self._db.commit()

    # ------------------------------------------------------------ small per-user values

    def pref(self, user_id: str, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM extra_prefs WHERE user_id = ? AND key = ?",
                                   (user_id, key)).fetchone()
        return row["value"] if row else None

    def set_pref(self, user_id: str, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO extra_prefs (user_id, key, value) VALUES (?, ?, ?) "
                             "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value", (user_id, key, value))
            self._db.commit()

    # ------------------------------------------------------------ picks shown per arm

    def add_picks_shown(self, arm: str, count: int, day: str) -> None:
        if count <= 0 or arm not in ("sie", "tfidf"):
            return
        with self._lock:
            self._db.execute("INSERT INTO extra_pick_views (day, arm, shown) VALUES (?, ?, ?) "
                             "ON CONFLICT(day, arm) DO UPDATE SET shown = shown + excluded.shown", (day, arm, count))
            self._db.commit()

    def picks_shown(self, since_day: Optional[str] = None) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT arm, SUM(shown) AS n FROM extra_pick_views WHERE day >= ? GROUP BY arm",
                                    (since_day or "",)).fetchall()
        return {r["arm"]: int(r["n"] or 0) for r in rows}

    # ------------------------------------------------------------ times the agent asked the person

    def add_ask(self, run_id: str, kind: str, user_id: str, at: Optional[datetime] = None) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO extra_asks (run_id, kind, user_id, asked_at) VALUES (?, ?, ?, ?)",
                             (run_id, kind, user_id, (at or _now()).isoformat()))
            self._db.commit()

    def asks(self, user_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            if user_id is None:
                rows = self._db.execute("SELECT * FROM extra_asks").fetchall()
            else:
                rows = self._db.execute("SELECT * FROM extra_asks WHERE user_id = ?", (user_id,)).fetchall()
        return [{"run_id": r["run_id"], "kind": r["kind"], "user_id": r["user_id"],
                 "asked_at": datetime.fromisoformat(r["asked_at"])} for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


_stores: dict[str, ExtraStore] = {}
_stores_lock = threading.Lock()


def get_extra_store() -> ExtraStore:
    """Same file as Part 5's store; one ExtraStore per path (tests switch paths between cases)."""
    try:
        from app.store import get_store
        path = get_store().path
    except Exception:
        path = os.getenv("DATABASE_PATH", "./event_agent.db")
    with _stores_lock:
        if path not in _stores:
            _stores[path] = ExtraStore(path)
        return _stores[path]
