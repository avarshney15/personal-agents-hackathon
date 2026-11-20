"""Part 8's thin layer over the other parts' code: users, runs, events, feed, telemetry, time.

Everything else in app/extra goes through here, so a name another part renames is fixed once.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request

from app import registry
from app.contracts import Event, RsvpRun, Site, User, WeekStatus

log = logging.getLogger("extra")

PT = ZoneInfo("America/Los_Angeles")
USER_COOKIE = "ea_user"

SITE_NAMES: dict[str, str] = {"luma": "Luma", "partiful": "Partiful", "meetup": "Meetup", "eventbrite": "Eventbrite"}
SITES: list[Site] = ["luma", "partiful", "meetup", "eventbrite"]


# ---------------------------------------------------------------- time


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local(dt: datetime) -> datetime:
    return aware(dt).astimezone(PT)


def today_start() -> datetime:
    return datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0)


def clock(dt: datetime) -> str:
    """6:30 (the canvas leaves am/pm off inside sentences)."""
    return local(dt).strftime("%-I:%M")


def clock_ampm(dt: datetime) -> str:
    """2:14 pm"""
    return local(dt).strftime("%-I:%M %p").lower()


def day_label(dt: datetime) -> str:
    """Nov 20"""
    return local(dt).strftime("%b %-d")


def event_end(event: Event) -> datetime:
    return aware(event.ends_at) if event.ends_at else aware(event.starts_at) + timedelta(hours=2)


# ---------------------------------------------------------------- users and the store


def store():
    from app.store import get_store
    return get_store()


def current_user(request: Request) -> User:
    """Part 5's dependency (cookie `ea_user`, header `X-User-Id`, else the demo user)."""
    try:
        from app.deps import current_user as dep
    except ImportError:
        from app.main import current_user as dep  # type: ignore[no-redef]
    return dep(request)


def get_user(user_id: str) -> Optional[User]:
    return store().get_user(user_id)


def save_user(user: User) -> User:
    return store().save_user(user)


def set_into(user: User, into: list[str]) -> User:
    s = store()
    if hasattr(s, "set_into"):
        return s.set_into(user.id, into) or user.model_copy(update={"into": into})
    return s.save_user(user.model_copy(update={"into": into}))


def list_runs(user_id: Optional[str] = None, since: Optional[datetime] = None) -> list[RsvpRun]:
    """Newest first. Uses Part 5's `list_runs` when it exists."""
    s = store()
    if hasattr(s, "list_runs"):
        runs = list(s.list_runs(user_id=user_id, since=since))
    elif user_id is not None:
        runs = list(s.runs_for_user(user_id, limit=1000))
    else:  # every person's runs (Ops): read the ids, then load each run through the store
        with s._lock:  # noqa: SLF001
            ids = [r[0] for r in s._db.execute("SELECT id FROM runs ORDER BY started_at DESC LIMIT 2000")]
        runs = [r for r in (s.get_run(i) for i in ids) if r]
    if since is not None:
        runs = [r for r in runs if aware(r.started_at) >= aware(since)]
    return sorted(runs, key=lambda r: aware(r.started_at), reverse=True)


def spent_today(user: User) -> float:
    s = store()
    if hasattr(s, "spent_today"):
        return float(s.spent_today(user.id))
    return float(sum(r.cost_usd for r in list_runs(user.id, since=today_start())))


# ---------------------------------------------------------------- events


def get_event(event_id: str) -> Optional[Event]:
    """The live catalog, else the fake catalog (demo runs may point at fake-* events)."""
    try:
        event = registry.get_catalog().get(event_id)
    except Exception:
        log.exception("catalog get %s failed", event_id)
        event = None
    if event is None:
        from app.catalog.fake import FakeCatalog
        event = FakeCatalog().get(event_id)
    return event


def catalog_events() -> list[Event]:
    try:
        return list(registry.get_catalog().events())
    except Exception:
        log.exception("catalog events failed")
        return []


def catalog_loaded_at() -> datetime:
    """When the events were read: the catalog file's time, else when this process started."""
    try:
        path = getattr(registry.get_catalog(), "path", None)
        if path is not None and path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        pass
    return PROCESS_STARTED


PROCESS_STARTED = now()


# ---------------------------------------------------------------- runs → week status


def week_status(run: RsvpRun) -> Optional[WeekStatus]:
    if run.status == "done":
        return "waitlist" if run.outcome == "waitlist" else "going"
    if run.status in ("queued", "running", "needs_you"):
        return "pending"
    return None  # failed


def latest_by_event(runs: list[RsvpRun]) -> dict[str, RsvpRun]:
    """Each event's newest run that still counts (not failed). `runs` newest first."""
    out: dict[str, RsvpRun] = {}
    for run in runs:
        if run.event_id not in out and week_status(run) is not None:
            out[run.event_id] = run
    return out


# ---------------------------------------------------------------- feed (Part 2)


def feed():
    try:
        return importlib.import_module("app.feed")
    except ImportError as exc:
        raise HTTPException(503, f"The feed isn't ready yet ({exc}).") from exc


def feed_or_none():
    try:
        return importlib.import_module("app.feed")
    except ImportError:
        return None


# ---------------------------------------------------------------- telemetry


def track(user_id: str, event: str, props: Optional[dict] = None) -> None:
    try:
        registry.get_telemetry().track(user_id, event, props or {})
    except Exception:
        log.exception("telemetry track %s failed", event)
