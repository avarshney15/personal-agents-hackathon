"""Seeded real SF events from data/events.json. Owned by Part 5.

`SeedCatalog()` raises FileNotFoundError when the file is missing, so the registry falls back
to the fake catalog. Events come from public Luma pages (see app/catalog/luma.py).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.contracts import Event

REPO_ROOT = Path(__file__).resolve().parents[3]


def events_path() -> Path:
    return Path(os.getenv("EVENTS_PATH") or REPO_ROOT / "data" / "events.json")


def load_events(path: Optional[Path] = None) -> list[Event]:
    path = Path(path or events_path())
    if not path.exists():
        raise FileNotFoundError(path)
    raw = json.loads(path.read_text())
    items = raw.get("events", []) if isinstance(raw, dict) else raw
    return [Event.model_validate(item) for item in items]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SeedCatalog:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or events_path())
        self._lock = threading.Lock()
        self._all: dict[str, Event] = {e.id: e for e in load_events(self.path)}

    def events(self) -> list[Event]:
        """Upcoming events only, soonest first."""
        now = datetime.now(timezone.utc)
        with self._lock:
            upcoming = [e for e in self._all.values() if _aware(e.starts_at) > now]
        return sorted(upcoming, key=lambda e: _aware(e.starts_at))

    def get(self, event_id: str) -> Optional[Event]:
        with self._lock:
            return self._all.get(event_id)

    def put(self, event: Event) -> Event:
        """Add or refresh one event in memory (e.g. after reading its Luma page)."""
        with self._lock:
            self._all[event.id] = event
        return event

    def refresh(self, event_id: str) -> Optional[Event]:
        """Re-read one event from its Luma page; keeps the old copy on any error."""
        from app.catalog.luma import read_event

        old = self.get(event_id)
        if not old or old.source != "luma":
            return old
        try:
            fresh = read_event(old.url)
        except Exception:
            return old
        if not fresh.tags:
            fresh.tags = old.tags
        return self.put(fresh)


def add_event(event: Event, path: Optional[Path] = None) -> None:
    """Insert or replace one event in data/events.json, kept sorted by start time."""
    path = Path(path or events_path())
    events = {e.id: e for e in load_events(path)} if path.exists() else {}
    events[event.id] = event
    ordered = sorted(events.values(), key=lambda e: _aware(e.starts_at))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([e.model_dump(mode="json") for e in ordered], indent=2,
                               ensure_ascii=False) + "\n")
