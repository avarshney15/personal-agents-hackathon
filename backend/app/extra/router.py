"""Routes for the updated canvas owned by Part 8 (backend/API.md, "Routes for the updated canvas").

Part 5 mounts `router` in app/main.py. Screens: Welcome (auth), Connect (sites, calendar),
Taste, Week, Ask, Event sheet, Activity, Ops.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app import registry
from app.contracts import (DEMO_USER_ID, ActivityResponse, AskResponse, EventDetail, FeedResponse, OpsSummary,
                           SiteConnection, TasteRequest, User, WeekResponse)
from app.demo import demo_user
from app.extra import data, reports, taps, week
from app.extra.store_extra import CODE_MINUTES, get_extra_store

log = logging.getLogger("extra")

COOKIE_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _tap() -> None:
    taps.install()


router = APIRouter(dependencies=[Depends(_tap)])
current_user = data.current_user

try:  # count telemetry from the start, not only after the first Part 8 request
    taps.install()
except Exception:  # pragma: no cover
    log.exception("telemetry tap failed at import")


# ---------------------------------------------------------------- Welcome: sign in with an emailed code


class AuthStart(BaseModel):
    email: str


class AuthVerify(BaseModel):
    email: str
    code: str


def _email(raw: str) -> str:
    email = raw.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "That doesn't look like an email address.")
    return email


def _dev_codes() -> bool:
    return os.getenv("AUTH_DEV_CODES", "1").strip().lower() in ("1", "true", "yes")


def _name_from(email: str) -> str:
    local = email.split("@")[0].split("+")[0]
    words = [w for w in re.split(r"[._\-]+", local) if w]
    return " ".join(w.capitalize() for w in words) or local


@router.post("/api/auth/start")
def auth_start(body: AuthStart) -> dict:
    email = _email(body.email)
    code = get_extra_store().new_code(email)
    out: dict = {"sent": True}
    if _dev_codes():
        log.warning("Sign-in code for %s: %s (valid %d minutes; no email provider yet)", email, code, CODE_MINUTES)
        out["dev_code"] = code
    return out


def _user_for(email: str) -> User:
    xs = get_extra_store()
    linked = xs.user_for_email(email)
    if linked:
        user = data.get_user(linked)
        if user:
            return user
    if email == os.getenv("DEMO_USER_EMAIL", "").strip().lower():
        s = data.store()
        user = s.ensure_user(demo_user()) if hasattr(s, "ensure_user") else (data.get_user(DEMO_USER_ID) or demo_user())
    else:
        user = data.get_user(email) or User(id=email, name=_name_from(email), email=email)
    if user.email != email:
        user = user.model_copy(update={"email": email})
    user = data.save_user(user)
    xs.link_email(email, user.id)
    return user


@router.post("/api/auth/verify", response_model=User)
def auth_verify(body: AuthVerify, response: Response) -> User:
    email = _email(body.email)
    problem = get_extra_store().check_code(email, body.code)
    if problem:
        raise HTTPException(401, problem)
    user = _user_for(email)
    response.set_cookie(data.USER_COOKIE, user.id, max_age=COOKIE_DAYS * 86400, httponly=True, samesite="lax",
                        path="/")
    data.track(user.id, "signed_in", {"new": not user.verified})
    return user


@router.post("/api/auth/logout")
def auth_logout(response: Response, user: User = Depends(current_user)) -> dict:
    response.delete_cookie(data.USER_COOKIE, path="/")
    data.track(user.id, "signed_out")
    return {"ok": True}


# ---------------------------------------------------------------- Connect: sites and calendar


def _site_or_404(site: str) -> str:
    site = site.strip().lower()
    if site not in data.SITES:
        raise HTTPException(404, f"No site {site}. Try one of: {', '.join(data.SITES)}.")
    return site


def _connections(user: User) -> list[SiteConnection]:
    week.ensure_demo_seed(user)
    state = get_extra_store().sites(user.id)
    counts: dict[str, int] = {}
    for event in data.catalog_events():
        counts[event.site] = counts.get(event.site, 0) + 1
    return [SiteConnection(site=s, name=data.SITE_NAMES[s], connected=state.get(s, False), event_count=counts.get(s, 0))
            for s in data.SITES]


@router.get("/api/sites", response_model=list[SiteConnection])
def get_sites(user: User = Depends(current_user)) -> list[SiteConnection]:
    return _connections(user)


def _set_site(user: User, site: str, connected: bool) -> SiteConnection:
    site = _site_or_404(site)
    week.ensure_demo_seed(user)
    get_extra_store().set_site(user.id, site, connected)
    data.track(user.id, "site_connected" if connected else "site_disconnected", {"site": site})
    return next(c for c in _connections(user) if c.site == site)


@router.post("/api/sites/{site}/connect", response_model=SiteConnection)
def connect_site(site: str, user: User = Depends(current_user)) -> SiteConnection:
    return _set_site(user, site, True)


@router.delete("/api/sites/{site}", response_model=SiteConnection)
def disconnect_site(site: str, user: User = Depends(current_user)) -> SiteConnection:
    return _set_site(user, site, False)


def _calendar_source() -> str:
    return "Google Calendar" if os.getenv("CALENDAR_SOURCE", "fake").strip().lower() == "airbyte" else "Demo calendar"


@router.get("/api/calendar")
def get_calendar(user: User = Depends(current_user)) -> dict:
    return {"connected": week.calendar_connected(user), "source": _calendar_source()}


@router.post("/api/calendar/connect")
def connect_calendar(user: User = Depends(current_user)) -> dict:
    get_extra_store().set_calendar(user.id, True)
    data.track(user.id, "calendar_connected", {"source": _calendar_source()})
    return {"connected": True, "source": _calendar_source()}


@router.delete("/api/calendar")
def disconnect_calendar(user: User = Depends(current_user)) -> dict:
    get_extra_store().set_calendar(user.id, False)
    data.track(user.id, "calendar_disconnected", {"source": _calendar_source()})
    return {"connected": False, "source": _calendar_source()}


# ---------------------------------------------------------------- Taste


def _merge(have: list[str], new: list[str]) -> list[str]:
    out, seen = list(have), {c.lower() for c in have}
    for chip in new:
        chip = (chip or "").strip()
        if chip and chip.lower() not in seen:
            out.append(chip)
            seen.add(chip.lower())
    return out


def _times_sentence(times: list[str]) -> str:
    parts = []
    for label in times:
        low = label.strip().lower()
        parts.append(f"in the {low}" if low in ("mornings", "afternoons", "evenings") else f"on {low}")
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Usually goes {joined}"


def _remember_times(user: User, times: list[str]) -> None:
    memory = registry.get_memory()
    xs = get_extra_store()
    old = xs.pref(user.id, "usual_times_fact")
    if old:
        try:
            memory.forget(user.id, old)
        except Exception:
            log.exception("forgetting old usual-times fact failed")
    fact_id = memory.remember(user.id, "said", _times_sentence(times),
                              f"You told the agent, {data.day_label(data.now())}")
    xs.set_pref(user.id, "usual_times_fact", fact_id)


@router.post("/api/taste", response_model=FeedResponse)
def save_taste(body: TasteRequest, user: User = Depends(current_user)) -> FeedResponse:
    feed = data.feed()
    text = body.text.strip()
    tapped = _merge([], body.chips)
    covered = {c.lower() for c in user.into}
    extra = [c for c in tapped if c.lower() not in covered]
    unsaid = [c for c in extra if c.lower() not in text.lower()]  # tapped, not in their own words
    said = "\n".join(part for part in (text, ", ".join(unsaid)) if part)  # one memory write, one read
    from_feed: list[str] = []
    if said:
        from_feed = list(feed.add_into(user, said) or [])
        user = data.get_user(user.id) or user

    into = _merge(user.into, [*from_feed, *extra])
    if into != user.into:
        user = data.set_into(user, into)

    times = _merge([], body.usual_times)
    if times and [t.lower() for t in times] != [t.lower() for t in user.usual_times]:
        user = data.save_user(user.model_copy(update={"usual_times": times}))
        try:
            _remember_times(user, times)
        except Exception:
            log.exception("remembering usual times failed for %s", user.id)

    data.track(user.id, "taste_saved", {"text": text, "chips": tapped, "usual_times": times, "into": user.into})
    return feed.build_feed(user, limit=20)


# ---------------------------------------------------------------- Week, Event sheet, Ask


@router.get("/api/week", response_model=WeekResponse)
def get_week(user: User = Depends(current_user)) -> WeekResponse:
    return week.build_week(user)


def _fallback_why(user: User, event) -> tuple[str, list, Optional[str]]:
    try:
        facts = registry.get_memory().recall(user.id, event, k=3)
    except Exception:
        log.exception("memory recall failed")
        facts = []
    why = " ".join(f"{f.text.rstrip('.')}." for f in facts[:2]) or event.description
    return why, facts, None


@router.get("/api/events/{event_id}/detail", response_model=EventDetail)
def event_detail(event_id: str, user: User = Depends(current_user)) -> EventDetail:
    event = data.get_event(event_id)
    if not event:
        raise HTTPException(404, f"No event {event_id}.")
    feed = data.feed_or_none()
    why, facts, tag = "", [], None
    if feed is not None and hasattr(feed, "event_why"):
        try:
            why, facts, tag = feed.event_why(user, event)
        except Exception:
            log.exception("event_why failed for %s", event_id)
    if not why and not facts:
        why, facts, tag = _fallback_why(user, event)
    status, run_id = week.status_for(user, event.id)
    data.track(user.id, "pick_opened", {"event_id": event.id, "via": "detail", "week_status": status})
    return EventDetail(event=event, tag=tag, why=why or "", remembered=list(facts or []),
                       clash=week.detail_clash(user, event), week_status=status)


class AskBody(BaseModel):
    text: str


@router.post("/api/ask", response_model=AskResponse)
def ask(body: AskBody, user: User = Depends(current_user)) -> AskResponse:
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Ask for something, like “agent hackathons next week, evenings”.")
    response = data.feed().ask(user, text)
    try:
        response = week.add_clash_notes(user, response)
    except Exception:
        log.exception("clash notes failed")
    data.track(user.id, "ask_used", {"text": text, "results": len(response.picks)})
    return response


# ---------------------------------------------------------------- Activity and Ops


@router.get("/api/activity", response_model=ActivityResponse)
def get_activity(user: User = Depends(current_user)) -> ActivityResponse:
    return reports.build_activity(user)


@router.get("/api/ops", response_model=OpsSummary)
def get_ops(sample: bool = False) -> OpsSummary:
    """Team dashboard. `?sample=1` shows the canvas's numbers throughout."""
    return reports.build_ops(sample=sample)
