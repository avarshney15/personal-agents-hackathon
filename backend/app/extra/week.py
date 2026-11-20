"""My week, calendar clashes, and the clash notes on Ask results."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from app import registry
from app.contracts import (DEMO_USER_ID, AskResponse, CalendarItem, Clash, Event, User, WeekDay, WeekItem,
                           WeekResponse, WeekStatus)
from app.extra import data
from app.extra.store_extra import get_extra_store

log = logging.getLogger("extra.week")

WEEK_DAYS = 7


# ---------------------------------------------------------------- connections (seeded for the demo user)


def ensure_demo_seed(user: User) -> None:
    """The demo user starts with Luma and the calendar connected."""
    if user.id != DEMO_USER_ID:
        return
    xs = get_extra_store()
    if not xs.sites(user.id):
        xs.set_site(user.id, "luma", True)
    if xs.calendar(user.id) is None:
        xs.set_calendar(user.id, True)


def calendar_connected(user: User) -> bool:
    ensure_demo_seed(user)
    return bool(get_extra_store().calendar(user.id))


def calendar_items(user: User, start: datetime, end: datetime) -> list[CalendarItem]:
    if not calendar_connected(user):
        return []
    try:
        return list(registry.get_calendar().items(user.id, start, end))
    except Exception:
        log.exception("calendar items failed for %s", user.id)
        return []


# ---------------------------------------------------------------- clashes


def _span(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, rest = divmod(minutes, 60)
    if hours == 1 and rest == 0:
        return "an hour"
    if rest == 0:
        return f"{hours} hours"
    return f"{hours} hour{'s' if hours > 1 else ''} {rest} minutes"


def _round5(minutes: float) -> int:
    return max(1, int(round(minutes / 5.0)) * 5) if minutes >= 5 else max(1, int(round(minutes)))


def clash_for(event: Event, items: list[CalendarItem], style: str = "week") -> Optional[Clash]:
    """The first calendar item overlapping the event, worded for the Week banner or the Event sheet."""
    start, end = data.aware(event.starts_at), data.event_end(event)
    overlapping = sorted((i for i in items if data.aware(i.starts_at) < end and data.aware(i.ends_at) > start),
                         key=lambda i: data.aware(i.starts_at))
    if not overlapping:
        return None
    item = overlapping[0]
    i_start, i_end = data.aware(item.starts_at), data.aware(item.ends_at)
    late = _round5((i_end - start).total_seconds() / 60) if i_start <= start else 0
    if style == "week":
        text = (f"{event.title} starts at {data.clock(start)}, and you have “{item.title}” "
                f"from {data.clock(i_start)} to {data.clock(i_end)}.")
    else:
        text = f"Clashes with “{item.title}”: {data.clock(i_start)} to {data.clock(i_end)} in your calendar."
        if late:
            text += f" You’d arrive about {_span(late)} late."
        else:
            missed = _round5((min(i_end, end) - i_start).total_seconds() / 60)
            text += f" You’d miss about {_span(missed)} of it."
    return Clash(event_id=event.id, item=item, minutes_late=late, text=text)


# ---------------------------------------------------------------- the user's events from runs


def user_events(user: User) -> list[tuple[Event, WeekStatus, str]]:
    """(event, status, run_id) for each event the agent RSVP'd or is RSVPing to, soonest first."""
    out = []
    for event_id, run in data.latest_by_event(data.list_runs(user.id)).items():
        event = data.get_event(event_id)
        status = data.week_status(run)
        if event and status:
            out.append((event, status, run.id))
    return sorted(out, key=lambda t: data.aware(t[0].starts_at))


def status_for(user: User, event_id: str) -> tuple[Optional[WeekStatus], Optional[str]]:
    run = data.latest_by_event(data.list_runs(user.id)).get(event_id)
    return (data.week_status(run), run.id) if run else (None, None)


def build_week(user: User) -> WeekResponse:
    first = data.today_start()
    last = first + timedelta(days=WEEK_DAYS)
    by_day: dict[str, list[WeekItem]] = {}
    shown: list[Event] = []
    for event, status, run_id in user_events(user):
        start = data.local(event.starts_at)
        if not (first <= start < last):
            continue
        by_day.setdefault(start.date().isoformat(), []).append(WeekItem(event=event, status=status, run_id=run_id))
        shown.append(event)

    today = first.date().isoformat()
    days = []
    for day in sorted(by_day):
        when = data.local(by_day[day][0].event.starts_at)
        days.append(WeekDay(date=day, label="Today" if day == today else data.day_label(when),
                            weekday=when.strftime("%A"), items=by_day[day]))

    clashes: list[Clash] = []
    if shown:
        items = calendar_items(user, first, last + timedelta(days=1))
        for event in shown:
            clash = clash_for(event, items, style="week")
            if clash:
                clashes.append(clash)
    return WeekResponse(days=days, clashes=clashes)


def detail_clash(user: User, event: Event) -> Optional[Clash]:
    start = data.aware(event.starts_at)
    items = calendar_items(user, start - timedelta(days=1), data.event_end(event) + timedelta(days=1))
    return clash_for(event, items, style="detail")


# ---------------------------------------------------------------- Ask: clash notes


def _overlaps(a: Event, b: Event) -> bool:
    return data.aware(a.starts_at) < data.event_end(b) and data.aware(b.starts_at) < data.event_end(a)


_WHERE = {
    "waitlist": "where you’re on the waitlist",
    "going": "which you’re going to",
    "pending": "which the agent is RSVPing you to",
    "invited": "which you’re invited to",
}


def add_clash_notes(user: User, response: AskResponse) -> AskResponse:
    """Appends "Clashes with Bouldering social, where you’re on the waitlist." to each clashing pick."""
    if not response.picks:
        return response
    now = data.now()
    mine = [(e, s) for e, s, _ in user_events(user) if data.event_end(e) > now]
    cal: list[CalendarItem] = []
    if response.picks:
        starts = [data.aware(p.event.starts_at) for p in response.picks]
        cal = calendar_items(user, min(starts) - timedelta(days=1), max(starts) + timedelta(days=2))
    picks = []
    for pick in response.picks:
        note = None
        for event, status in mine:
            if event.id != pick.event.id and _overlaps(event, pick.event):
                note = f"Clashes with {event.title}, {_WHERE.get(status, 'which is on your week')}."
                break
        if note is None:
            clash = clash_for(pick.event, cal, style="week")
            if clash:
                note = f"Clashes with “{clash.item.title}” in your calendar."
        if note and note not in pick.why:
            pick = pick.model_copy(update={"why": f"{pick.why.rstrip()} {note}".strip()})
        picks.append(pick)
    return response.model_copy(update={"picks": picks})
