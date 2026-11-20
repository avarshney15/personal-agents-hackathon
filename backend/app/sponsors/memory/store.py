"""Local source of truth for remembered facts (SQLite).

The Memory screen, forget, and taste read from here. Cognee holds a copy of each fact for
search by meaning; the cognee_* columns keep what we need to delete that copy.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.contracts import Fact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    kind              TEXT NOT NULL,
    text              TEXT NOT NULL,
    from_text         TEXT NOT NULL,
    event_id          TEXT,
    created_at        TEXT NOT NULL,
    cognee_status     TEXT NOT NULL DEFAULT 'pending',  -- pending | indexed | failed
    cognee_data_id    TEXT,
    cognee_dataset_id TEXT,
    cognee_error      TEXT
);
CREATE INDEX IF NOT EXISTS facts_by_user ON facts (user_id, created_at);
"""

_ORDER = "ORDER BY created_at DESC, rowid DESC"


def row_to_fact(row) -> Fact:
    return Fact(id=row["id"], kind=row["kind"], text=row["text"], from_text=row["from_text"],
                event_id=row["event_id"], created_at=datetime.fromisoformat(row["created_at"]))


class FactStore:
    """Thread-safe: one connection guarded by a lock (FastAPI calls from worker threads)."""

    def __init__(self, path: str) -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)

    def add(self, user_id: str, fact: Fact) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO facts (id, user_id, kind, text, from_text, event_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fact.id, user_id, fact.kind, fact.text, fact.from_text, fact.event_id,
                 fact.created_at.isoformat()))

    def facts(self, user_id: str) -> list[Fact]:
        """Newest first."""
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM facts WHERE user_id = ? {_ORDER}",
                                      (user_id,)).fetchall()
        return [row_to_fact(r) for r in rows]

    def rows(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(f"SELECT * FROM facts WHERE user_id = ? {_ORDER}",
                                      (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def get(self, user_id: str, fact_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE user_id = ? AND id = ?",
                                     (user_id, fact_id)).fetchone()
        return dict(row) if row else None

    def delete(self, user_id: str, fact_id: str) -> Optional[dict]:
        """Deletes one fact and returns its row (with the Cognee ids), or None."""
        with self._lock:
            row = self.get(user_id, fact_id)
            if row:
                self._conn.execute("DELETE FROM facts WHERE user_id = ? AND id = ?",
                                   (user_id, fact_id))
        return row

    def delete_all(self, user_id: str) -> list[dict]:
        with self._lock:
            rows = self.rows(user_id)
            self._conn.execute("DELETE FROM facts WHERE user_id = ?", (user_id,))
        return rows

    def mark_indexed(self, fact_id: str, data_id: Optional[str], dataset_id: Optional[str]) -> bool:
        """Records the Cognee ids. False if the fact was forgotten in the meantime."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE facts SET cognee_status = 'indexed', cognee_data_id = ?, "
                "cognee_dataset_id = ?, cognee_error = NULL WHERE id = ?",
                (data_id, dataset_id, fact_id))
        return cur.rowcount > 0

    def indexed_data_ids(self, user_id: str) -> set[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cognee_data_id FROM facts WHERE user_id = ? AND cognee_data_id IS NOT NULL",
                (user_id,)).fetchall()
        return {r["cognee_data_id"] for r in rows}

    def set_data_id(self, fact_id: str, data_id: str) -> None:
        """Repairs a fact's Cognee data id from what search reports for it."""
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET cognee_data_id = ? WHERE id = ? AND cognee_status = 'indexed'",
                (data_id, fact_id))

    def mark_failed(self, fact_id: str, error: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET cognee_status = 'failed', cognee_error = ? WHERE id = ?",
                (error[:500], fact_id))

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT cognee_status, COUNT(*) AS n FROM facts GROUP BY cognee_status").fetchall()
        return {r["cognee_status"]: r["n"] for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
