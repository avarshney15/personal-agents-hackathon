"""Demo calendar: the canvas's "Call with Sam", 6:00 to 7:00 pm PT. Owned by Part 8.

The call sits on the coming Tuesday (the canvas's Nov 24), unless the loaded catalog has an
upcoming "Agent builders" event (DEMO_CALENDAR_ANCHOR; empty turns this off) in the next 8 days:
then it moves to half an hour before that event's start, so the Week and Event screens show the
canvas's clash ("You'd arrive about 30 minutes late"). With the seeded SF catalog there is no such
event, and the Tuesday call clashes with that evening's real events.
Events the agent adds ("Added to your calendar") are kept apart and never clash with themselves.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.contracts import CalendarItem, Event

PT = timezone(timedelta(hours=-8))
DEFAULT_ANCHOR = "Agent builders"


def _next_weekday(weekday: int) -> datetime:
    today = datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _anchor_event(title_part: str) -> Optional[Event]:
    """Soonest upcoming event (next 8 days) whose title contains `title_part`."""
    try:
        from app import registry
        events = list(registry.get_catalog().events())
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    want = title_part.lower()
    hits = [e for e in events if want in e.title.lower() and now < _aware(e.starts_at) < now + timedelta(days=8)]
    return min(hits, key=lambda e: _aware(e.starts_at)) if hits else None


class FakeCalendar:
    def __init__(self, items: Optional[list[CalendarItem]] = None, anchor: Optional[str] = None) -> None:
        self._fixed = items
        self._anchor = os.getenv("DEMO_CALENDAR_ANCHOR", DEFAULT_ANCHOR) if anchor is None else anchor
        self.added: list[str] = []

    def _items(self) -> list[CalendarItem]:
        if self._fixed is not None:
            return list(self._fixed)
        event = _anchor_event(self._anchor) if self._anchor.strip() else None
        if event:
            start = _aware(event.starts_at) - timedelta(minutes=30)
            return [CalendarItem(id="cal-sam", title="Call with Sam", starts_at=start,
                                 ends_at=start + timedelta(hours=1))]
        tuesday = _next_weekday(1)
        return [CalendarItem(id="cal-sam", title="Call with Sam", starts_at=tuesday.replace(hour=18),
                             ends_at=tuesday.replace(hour=19))]

    def items(self, user_id: str, start: datetime, end: datetime) -> list[CalendarItem]:
        start, end = _aware(start), _aware(end)
        return [i for i in self._items() if _aware(i.ends_at) > start and _aware(i.starts_at) < end]

    def add_event(self, user_id: str, event: Event) -> bool:
        self.added.append(event.id)
        return True
