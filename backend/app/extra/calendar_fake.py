"""Demo calendar: the canvas's "Call with Sam" on the coming Tuesday, 6:00 to 7:00 pm PT.
Owned by Part 8 from here on; keep the class name and interface."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.contracts import CalendarItem, Event

PT = timezone(timedelta(hours=-8))


def _next_weekday(weekday: int) -> datetime:
    today = datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0)
    return today + timedelta(days=(weekday - today.weekday()) % 7 or 7)


class FakeCalendar:
    def __init__(self) -> None:
        tuesday = _next_weekday(1)
        self._items = [CalendarItem(id="cal-sam", title="Call with Sam",
                                    starts_at=tuesday.replace(hour=18),
                                    ends_at=tuesday.replace(hour=19))]
        self.added: list[str] = []

    def items(self, user_id: str, start: datetime, end: datetime) -> list[CalendarItem]:
        return [i for i in self._items if i.ends_at > start and i.starts_at < end]

    def add_event(self, user_id: str, event: Event) -> bool:
        self.added.append(event.id)
        return True
