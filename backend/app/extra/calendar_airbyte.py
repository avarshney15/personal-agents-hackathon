"""Google Calendar through Airbyte (PyAirbyte, `source-google-calendar`), read only. Owned by Part 8.

CALENDAR_SOURCE=airbyte picks this (app/registry.py). One Google account for the whole app,
from env:
    GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, GOOGLE_CALENDAR_REFRESH_TOKEN
        your own Google Cloud OAuth app (the connector's "manual" auth); the refresh token needs
        the calendar.readonly scope
    GOOGLE_CALENDAR_ID      default "primary"
    AIRBYTE_PYTHON          default backend/.venv-airbyte/bin/python (PyAirbyte lives in its own venv;
                            its pins clash with the app's, see airbyte_read.py)
    CALENDAR_CACHE_SECONDS  default 300

The connector returns Google's event resources as they are (recurring events once, with their
RRULE; `singleEvents=false`), so this expands simple recurrences itself, and skips cancelled,
"free" (transparent) and all-day events, which don't make you late for anything.
add_event returns False: the Airbyte source can't write, so nothing is added to Google Calendar.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from app.contracts import CalendarItem, Event

log = logging.getLogger("extra.calendar_airbyte")

BACKEND = Path(__file__).resolve().parents[2]
READER = Path(__file__).resolve().with_name("airbyte_read.py")
FIRST_LOAD_WAIT_S = 25.0
MARKER = "EVENTS_JSON "  # same as airbyte_read.MARKER (that file runs in another venv)

Reader = Callable[[dict], list[dict]]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _when(value) -> Optional[datetime]:
    """Google's {dateTime, date, timeZone}; None for all-day (date only) or missing."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or not value.get("dateTime"):
        return None
    try:
        return _aware(datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00")))
    except ValueError:
        return None


def subprocess_reader(config: dict) -> list[dict]:
    python = os.getenv("AIRBYTE_PYTHON") or str(BACKEND / ".venv-airbyte" / "bin" / "python")
    if not Path(python).exists():
        raise RuntimeError(f"No PyAirbyte venv at {python}; see app/extra/airbyte_read.py")
    proc = subprocess.run([python, "-I", str(READER)], input=json.dumps(config), capture_output=True, text=True,
                          timeout=180, cwd=str(BACKEND), env={**os.environ, "DO_NOT_TRACK": "1"})
    if proc.returncode != 0:
        raise RuntimeError(f"airbyte_read failed ({proc.returncode}): {proc.stderr.strip()[-600:]}")
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(MARKER)]
    if not lines:
        raise RuntimeError(f"airbyte_read printed no events: {proc.stdout.strip()[-300:]}")
    return json.loads(lines[-1][len(MARKER):])


def config_from_env() -> dict:
    missing = [k for k in ("GOOGLE_CALENDAR_CLIENT_ID", "GOOGLE_CALENDAR_CLIENT_SECRET",
                           "GOOGLE_CALENDAR_REFRESH_TOKEN") if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"CALENDAR_SOURCE=airbyte needs {', '.join(missing)}")
    return {
        "credentials": {"auth_type": "manual", "client_id": os.environ["GOOGLE_CALENDAR_CLIENT_ID"],
                        "client_secret": os.environ["GOOGLE_CALENDAR_CLIENT_SECRET"],
                        "refresh_token": os.environ["GOOGLE_CALENDAR_REFRESH_TOKEN"]},
        "calendarid": os.getenv("GOOGLE_CALENDAR_ID", "primary") or "primary",
    }


def to_items(records: list[dict], start: datetime, end: datetime) -> list[CalendarItem]:
    """Busy, timed events overlapping [start, end), recurrences expanded."""
    out: list[CalendarItem] = []
    moved = {(r.get("recurringEventId"), str(_when(r.get("originalStartTime")))) for r in records
             if r.get("recurringEventId")}  # instances edited or cancelled one by one
    for r in records:
        if r.get("status") == "cancelled" or r.get("transparency") == "transparent":
            continue
        s, e = _when(r.get("start")), _when(r.get("end"))
        if s is None or e is None:
            continue
        title = r.get("summary") or "Busy"
        starts = [s]
        if r.get("recurrence"):
            starts = _expand(r["recurrence"], s, start - (e - s), end)
            starts = [x for x in starts if (r.get("id"), str(x)) not in moved]
        for i, x in enumerate(starts):
            y = x + (e - s)
            if y > start and x < end:
                out.append(CalendarItem(id=f"{r.get('id')}-{i}" if len(starts) > 1 else str(r.get("id")),
                                        title=title, starts_at=x, ends_at=y))
    return sorted(out, key=lambda i: i.starts_at)


def _expand(recurrence: list[str], first: datetime, start: datetime, end: datetime) -> list[datetime]:
    try:
        from dateutil.rrule import rrulestr
        rules = rrulestr("\n".join(recurrence), dtstart=first, forceset=True)
        return [_aware(x) for x in rules.between(start, end, inc=True)]
    except Exception:
        log.warning("couldn't expand recurrence %s", recurrence)
        return [first]


class AirbyteCalendar:
    def __init__(self, reader: Optional[Reader] = None, config: Optional[dict] = None,
                 cache_seconds: Optional[float] = None) -> None:
        self._reader = reader or subprocess_reader
        self._config = config
        self._ttl = float(os.getenv("CALENDAR_CACHE_SECONDS", "300") if cache_seconds is None else cache_seconds)
        self._records: Optional[list[dict]] = None
        self._loaded_at = 0.0
        self._lock = threading.Lock()
        self._loading: Optional[threading.Thread] = None
        self.error: Optional[str] = None

    def _load(self) -> None:
        try:
            records = self._reader(self._config if self._config is not None else config_from_env())
            with self._lock:
                self._records, self._loaded_at, self.error = records, time.time(), None
            log.info("read %d Google Calendar events through Airbyte", len(records))
        except Exception as exc:
            with self._lock:
                self.error, self._loaded_at = str(exc), time.time()
            log.error("Google Calendar through Airbyte failed: %s", exc)

    def refresh(self, wait: Optional[float] = None) -> None:
        """Re-reads in the background (one read at a time); waits up to `wait` seconds."""
        with self._lock:
            if self._loading is None or not self._loading.is_alive():
                self._loading = threading.Thread(target=self._load, name="airbyte-calendar", daemon=True)
                self._loading.start()
            thread = self._loading
        if wait:
            thread.join(wait)

    def items(self, user_id: str, start: datetime, end: datetime) -> list[CalendarItem]:
        if self._records is None:
            self.refresh(wait=FIRST_LOAD_WAIT_S)
        elif time.time() - self._loaded_at > self._ttl:
            self.refresh()  # serve what we have meanwhile
        with self._lock:
            records = list(self._records or [])
        return to_items(records, _aware(start), _aware(end))

    def add_event(self, user_id: str, event: Event) -> bool:
        return False  # read only
