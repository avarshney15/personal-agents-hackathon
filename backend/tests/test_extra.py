"""Part 8: the routes for the updated canvas, on a small app with a stand-in feed (Part 2)."""

from __future__ import annotations

import json
import sys
import types
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import contracts as c
from app import registry
from app.catalog.fake import FakeCatalog
from app.extra.calendar_fake import FakeCalendar
from app.sponsors.memory.fake import FakeMemory
from app.sponsors.telemetry.fake import FakeTelemetry


# ---------------------------------------------------------------- stand-ins


def _stub_feed(memory: FakeMemory) -> types.ModuleType:
    """Same names as app.feed (Part 2): build_feed, add_into, ask, event_why."""
    feed = types.ModuleType("app.feed")
    feed.calls = []
    known = ["AI agents", "Climbing", "Hackathons", "Design", "Music"]

    def add_into(user, text):
        feed.calls.append(("add_into", text))
        memory.remember(user.id, "said", text, "You told the agent, today")
        return [k for k in known if k.lower() in text.lower()]

    def build_feed(user, limit=20):
        picks = [c.Pick(event=e, score=1.0, why="Because.", tag="Match", arm="sie")
                 for e in FakeCatalog().events()[:limit]]
        return c.FeedResponse(picks=picks, into=user.into, arm="sie")

    def ask(user, text):
        events = {e.id: e for e in FakeCatalog().events()}
        bould = events["fake-bouldering"]
        clashing = c.Event(id="voice-hack", title="Voice agents hack evening", starts_at=bould.starts_at,
                           url="https://lu.ma/voice")
        picks = [c.Pick(event=events["fake-evals-workshop"], score=0.9, why="Hands-on, small.", tag="Strong match",
                        arm="sie"),
                 c.Pick(event=clashing, score=0.5, why="Voice agents, like you said.", tag="Match", arm="sie")]
        return c.AskResponse(query=text, summary="Two fit.", filters="Next week", picks=picks)

    def event_why(user, event):
        facts = memory.recall(user.id, event)
        return "You went to 3 agent hackathons this autumn.", facts, "Strong match"

    feed.add_into, feed.build_feed, feed.ask, feed.event_why = add_into, build_feed, ask, event_why
    return feed


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("AUTH_DEV_CODES", "1")
    monkeypatch.delenv("CALENDAR_SOURCE", raising=False)
    monkeypatch.delenv("DEMO_USER_EMAIL", raising=False)
    monkeypatch.setenv("REHEARSALS_PATH", str(tmp_path / "rehearsals.json"))
    from app.store import get_store
    get_store.cache_clear()

    memory, telemetry, catalog = FakeMemory(), FakeTelemetry(), FakeCatalog()
    bb = catalog.get("fake-agent-builders")
    sam = c.CalendarItem(id="cal-sam", title="Call with Sam", starts_at=bb.starts_at - timedelta(minutes=30),
                         ends_at=bb.starts_at + timedelta(minutes=30))
    calendar = FakeCalendar(items=[sam])
    monkeypatch.setattr(registry, "get_memory", lambda: memory)
    monkeypatch.setattr(registry, "get_telemetry", lambda: telemetry)
    monkeypatch.setattr(registry, "get_catalog", lambda: catalog)
    monkeypatch.setattr(registry, "get_calendar", lambda: calendar)
    feed = _stub_feed(memory)
    monkeypatch.setitem(sys.modules, "app.feed", feed)

    from app.extra.router import router
    app = FastAPI()
    app.include_router(router)
    yield types.SimpleNamespace(client=TestClient(app), memory=memory, telemetry=telemetry, catalog=catalog,
                                feed=feed, store=get_store(), calendar=calendar)
    get_store.cache_clear()


def _run(env, event_id, status="done", outcome="going", cost=0.05, user_id=c.DEMO_USER_ID, need=None,
         model="claude-haiku-4-5", arm="sie"):
    """A stored run as the worker would leave it."""
    store = env.store
    run = store.create_run(user_id, event_id, arm=arm)
    for key in c.STEP_ORDER:
        store.save_step(c.RsvpStep(run_id=run.id, key=key, title=key, detail="", state="done"))
    if status in ("queued", "running"):
        store.set_status(run.id, status)
    else:
        store.finish_run(run.id, c.RsvpResult(status=status, outcome=outcome if status == "done" else None,
                                              cost_usd=cost, model=model, need=need))
    return store.get_run(run.id)


def _events(env):
    return {e["what"]: e for e in env.client.get("/api/activity").json()["items"]}


# ---------------------------------------------------------------- Welcome: auth


def test_auth_code_flow_sets_cookie_and_finds_user(env):
    cl = env.client
    assert cl.post("/api/auth/start", json={"email": "not-an-email"}).status_code == 422
    r = cl.post("/api/auth/start", json={"email": " Dev.Patel@Example.com "})
    assert r.status_code == 200 and r.json()["sent"] is True
    code = r.json()["dev_code"]
    assert len(code) == 6 and code.isdigit()

    wrong = "000000" if code != "000000" else "111111"
    assert cl.post("/api/auth/verify", json={"email": "dev.patel@example.com", "code": wrong}).status_code == 401
    r = cl.post("/api/auth/verify", json={"email": "dev.patel@example.com", "code": code})
    assert r.status_code == 200
    user = c.User.model_validate(r.json())
    assert user.email == "dev.patel@example.com" and user.name == "Dev Patel"
    set_cookie = r.headers["set-cookie"].lower()
    assert "ea_user=" in set_cookie and "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert env.store.get_user(user.id).email == "dev.patel@example.com"
    # the code is used up
    assert cl.post("/api/auth/verify", json={"email": "dev.patel@example.com", "code": code}).status_code == 401

    # signing in again finds the same user
    code2 = cl.post("/api/auth/start", json={"email": "dev.patel@example.com"}).json()["dev_code"]
    again = cl.post("/api/auth/verify", json={"email": "dev.patel@example.com", "code": code2}).json()
    assert again["id"] == user.id

    r = cl.post("/api/auth/logout")
    assert r.json() == {"ok": True} and 'ea_user=""' in r.headers["set-cookie"]


def test_auth_no_dev_code_when_off_and_demo_email(env, monkeypatch):
    monkeypatch.setenv("AUTH_DEV_CODES", "0")
    r = env.client.post("/api/auth/start", json={"email": "maya@example.com"})
    assert r.json() == {"sent": True}
    monkeypatch.setenv("AUTH_DEV_CODES", "1")
    monkeypatch.setenv("DEMO_USER_EMAIL", "maya@example.com")
    code = env.client.post("/api/auth/start", json={"email": "maya@example.com"}).json()["dev_code"]
    user = env.client.post("/api/auth/verify", json={"email": "maya@example.com", "code": code}).json()
    assert user["id"] == c.DEMO_USER_ID and user["email"] == "maya@example.com"


def test_auth_code_expires_and_locks(env):
    from app.extra.store_extra import get_extra_store
    xs = get_extra_store()
    xs.new_code("a@b.co")
    for _ in range(5):
        assert xs.check_code("a@b.co", "x") == "That code isn't right."
    assert "Too many tries" in xs.check_code("a@b.co", "x")
    code = xs.new_code("a@b.co")
    xs._db.execute("UPDATE extra_auth_codes SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert "expired" in xs.check_code("a@b.co", code)


# ---------------------------------------------------------------- Connect: sites and calendar


def test_sites_demo_seed_and_toggle(env):
    sites = env.client.get("/api/sites").json()
    assert [s["name"] for s in sites] == ["Luma", "Partiful", "Meetup", "Eventbrite"]
    luma = sites[0]
    assert luma["connected"] is True and luma["event_count"] == len(env.catalog.events())
    assert not any(s["connected"] for s in sites[1:])

    r = env.client.post("/api/sites/partiful/connect")
    assert r.json() == {"site": "partiful", "name": "Partiful", "connected": True, "event_count": 0}
    assert ("demo", "site_connected", {"site": "partiful"}) in env.telemetry.events
    assert env.client.delete("/api/sites/luma").json()["connected"] is False
    assert [s["connected"] for s in env.client.get("/api/sites").json()] == [False, True, False, False]
    assert env.client.post("/api/sites/myspace/connect").status_code == 404

    other = env.client.get("/api/sites", headers={"X-User-Id": "u2"}).json()
    assert not any(s["connected"] for s in other)


def test_calendar_connect(env, monkeypatch):
    assert env.client.get("/api/calendar").json() == {"connected": True, "source": "Demo calendar"}
    h = {"X-User-Id": "u2"}
    assert env.client.get("/api/calendar", headers=h).json()["connected"] is False
    assert env.client.post("/api/calendar/connect", headers=h).json() == {"connected": True, "source": "Demo calendar"}
    assert env.client.get("/api/calendar", headers=h).json()["connected"] is True
    monkeypatch.setenv("CALENDAR_SOURCE", "airbyte")
    assert env.client.get("/api/calendar", headers=h).json()["source"] == "Google Calendar"


def test_fake_calendar_anchors_call_to_agent_builders(monkeypatch):
    monkeypatch.setattr(registry, "get_catalog", lambda: FakeCatalog())
    event = FakeCatalog().get("fake-agent-builders")
    cal = FakeCalendar()
    items = cal.items("demo", event.starts_at - timedelta(days=1), event.starts_at + timedelta(days=1))
    assert items and items[0].title == "Call with Sam"
    assert items[0].starts_at == event.starts_at - timedelta(minutes=30)
    tuesday = FakeCalendar(anchor="").items("demo", event.starts_at - timedelta(days=3),
                                            event.starts_at + timedelta(days=9))
    assert tuesday[0].starts_at.weekday() == 1 and tuesday[0].starts_at.hour == 18
    assert isinstance(cal, c.Calendar)


# ---------------------------------------------------------------- Taste


def test_taste_saves_text_chips_and_times(env):
    body = {"text": "Climbing and AI agents lately. Not big networking mixers.",
            "chips": ["AI agents", "Climbing", "Hackathons"], "usual_times": ["Weekday evenings"]}
    r = env.client.post("/api/taste", json=body)
    assert r.status_code == 200
    feed = c.FeedResponse.model_validate(r.json())
    assert sorted(x.lower() for x in feed.into) == ["ai agents", "climbing", "hackathons"]
    assert env.feed.calls == [("add_into", body["text"] + "\nHackathons")]
    user = env.store.get_user("demo")
    assert user.usual_times == ["Weekday evenings"]
    said = [f.text for f in env.memory.facts("demo").said]
    assert "Usually goes on weekday evenings" in said
    assert any(e[1] == "taste_saved" for e in env.telemetry.events)

    env.client.post("/api/taste", json={"usual_times": ["Weekend days", "Mornings"]})
    said = [f.text for f in env.memory.facts("demo").said]
    assert "Usually goes on weekend days and in the mornings" in said
    assert "Usually goes on weekday evenings" not in said  # replaced, not piled up


# ---------------------------------------------------------------- Week and the Event sheet


def test_week_groups_runs_and_finds_clash(env):
    going = _run(env, "fake-agent-builders")
    _run(env, "fake-bouldering", outcome="waitlist")
    _run(env, "fake-evals-workshop", status="running")
    _run(env, "fake-ceramics", status="failed")  # failed runs aren't on the week
    _run(env, "fake-agent-builders", status="done", user_id="someone-else")

    week = c.WeekResponse.model_validate(env.client.get("/api/week").json())
    items = {i.event.id: i for d in week.days for i in d.items}
    assert {k: v.status for k, v in items.items()} == {
        "fake-agent-builders": "going", "fake-bouldering": "waitlist", "fake-evals-workshop": "pending"}
    assert items["fake-agent-builders"].run_id == going.id
    assert [d.date for d in week.days] == sorted(d.date for d in week.days)
    bb = env.catalog.get("fake-agent-builders")
    from app.extra.data import local
    day = next(d for d in week.days if d.items[0].event.id == "fake-agent-builders")
    assert day.label == local(bb.starts_at).strftime("%b %-d") and day.weekday == local(bb.starts_at).strftime("%A")

    assert len(week.clashes) == 1
    clash = week.clashes[0]
    assert clash.event_id == "fake-agent-builders" and clash.minutes_late == 30
    assert clash.text == "Agent builders night starts at 6:30, and you have “Call with Sam” from 6:00 to 7:00."


def test_week_today_label_and_no_clash_without_calendar(env):
    from app.extra.data import today_start
    today_event = c.Event(id="today-hack", title="Personal Agents Hack", url="https://lu.ma/x",
                          starts_at=today_start() + timedelta(hours=9, minutes=30))
    env.catalog.get = lambda eid, _g=env.catalog.get: today_event if eid == "today-hack" else _g(eid)
    _run(env, "today-hack", user_id="u2")
    _run(env, "fake-agent-builders", user_id="u2")
    week = env.client.get("/api/week", headers={"X-User-Id": "u2"}).json()
    assert week["days"][0]["label"] == "Today" and week["days"][0]["items"][0]["event"]["id"] == "today-hack"
    assert week["clashes"] == []  # u2 never connected a calendar


def test_event_detail_with_clash_and_status(env):
    r = env.client.get("/api/events/fake-agent-builders/detail")
    assert r.status_code == 200
    d = c.EventDetail.model_validate(r.json())
    assert d.tag == "Strong match" and d.why.startswith("You went to 3")
    assert d.remembered  # demo facts aren't seeded here, but event_why's facts pass through as a list
    assert d.clash and d.clash.text == ("Clashes with “Call with Sam”: 6:00 to 7:00 in your calendar. "
                                        "You’d arrive about 30 minutes late.")
    assert d.week_status is None
    _run(env, "fake-agent-builders", status="needs_you",
         need=c.RunNeed(kind="code", title="Enter the code", detail="", step_key="register"))
    assert env.client.get("/api/events/fake-agent-builders/detail").json()["week_status"] == "pending"
    assert env.client.get("/api/events/nope/detail").status_code == 404


@pytest.fixture(autouse=True)
def _demo_facts(request):
    """Seed the canvas's demo facts once the env fixture's memory exists."""
    if "env" in request.fixturenames:
        from app.demo import seed_demo_memory
        seed_demo_memory(request.getfixturevalue("env").memory)


def test_event_detail_without_feed_falls_back_to_memory(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.feed", None)  # import of app.feed now fails
    d = env.client.get("/api/events/fake-agent-builders/detail").json()
    assert d["remembered"] and d["why"] and d["tag"] is None


# ---------------------------------------------------------------- Ask


def test_ask_adds_clash_note_for_waitlisted_event(env):
    _run(env, "fake-bouldering", outcome="waitlist")
    r = env.client.post("/api/ask", json={"text": "agent hack nights next week"})
    assert r.status_code == 200
    res = c.AskResponse.model_validate(r.json())
    whys = {p.event.id: p.why for p in res.picks}
    assert whys["voice-hack"] == ("Voice agents, like you said. "
                                  "Clashes with Bouldering social, where you’re on the waitlist.")
    assert whys["fake-evals-workshop"] == "Hands-on, small."
    assert env.client.post("/api/ask", json={"text": "  "}).status_code == 422


def test_routes_say_503_without_feed(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "app.feed", None)
    assert env.client.post("/api/ask", json={"text": "x"}).status_code == 503
    assert env.client.post("/api/taste", json={"text": "x"}).status_code == 503


# ---------------------------------------------------------------- Activity


def test_activity_rows_code_ask_and_spend(env):
    r1 = _run(env, "fake-agent-builders", cost=0.06)
    _run(env, "fake-bouldering", outcome="waitlist", cost=0.05)
    _run(env, "fake-evals-workshop", status="needs_you",
         need=c.RunNeed(kind="code", title="Enter the code", detail="", step_key="register"), cost=0.02)
    _run(env, "fake-ceramics", status="failed", cost=0.0)
    # the worker tracks this when the agent asks mid-run; the tap records it
    from app import extra  # noqa: F401
    from app.extra import taps
    taps.install()
    registry.get_telemetry().track("demo", "rsvp_needs_you", {"run_id": r1.id, "event_id": r1.event_id,
                                                              "kind": "code", "waiting": True})

    act = c.ActivityResponse.model_validate(env.client.get("/api/activity").json())
    rows = {i.what: i for i in act.items}
    assert rows["RSVP · Agent builders night"].result == "Going" and rows["RSVP · Agent builders night"].tone == "ok"
    assert rows["RSVP · Agent builders night"].where.startswith("Luma · ")
    assert rows["RSVP · Agent builders night"].where.endswith(" · 5 steps")
    assert rows["Joined waitlist · Bouldering social"].result == "Waitlist"
    assert rows["Joined waitlist · Bouldering social"].tone == "wait"
    assert rows["RSVP · Evals for agents, a workshop"].result == "Needs you"
    assert rows["RSVP · Ceramics for beginners"].result == "Failed"
    assert rows["Asked you for a code"].result == "Answered" and rows["Asked you for a code"].tone == "needs"
    assert rows["Asked you for a code"].run_id == r1.id
    assert rows["Read your events"].result == f"{len(env.catalog.events())} events"
    assert rows["Read your events"].where.startswith("Luma · ")
    assert [i.at for i in act.items] == sorted((i.at for i in act.items), reverse=True)
    assert act.spend.spent_usd == pytest.approx(0.13) and act.spend.limit_usd == 2.0


# ---------------------------------------------------------------- Ops


def test_ops_from_runs_with_sample_fallbacks(env):
    _run(env, "fake-agent-builders", cost=0.06)
    _run(env, "fake-bouldering", outcome="waitlist", cost=0.04, user_id="u2", arm="tfidf")
    _run(env, "fake-evals-workshop", status="needs_you",
         need=c.RunNeed(kind="code", title="Enter the code", detail="", step_key="register"), cost=0.02)
    _run(env, "fake-ceramics", status="failed", cost=0.0)

    ops = c.OpsSummary.model_validate(env.client.get("/api/ops").json())
    assert ops.runs_today == 4
    assert ops.confirmed_pct == 50.0  # 2 done with read-back, of 4 finished
    assert ops.handed_to_person == 1
    assert ops.cost_per_rsvp_usd == pytest.approx(0.06)  # $0.12 over 2 RSVPs
    assert ops.sample is True  # no picks shown yet, no rehearsals file
    assert [a.rsvp_rate for a in ops.arms] == [0.18, 0.11]
    assert len(ops.recent) == 4
    top = {r.run: r for r in ops.recent}
    assert top["RSVP · Agent builders night"].model == "Claude Haiku" and top["RSVP · Agent builders night"].ok
    assert top["Waitlist · Bouldering social"].outcome == "Waitlist"
    assert top["RSVP · Evals for agents, a workshop"].outcome == "Needs code"
    assert not top["RSVP · Evals for agents, a workshop"].ok

    from app.extra import taps
    taps.install()
    tel = registry.get_telemetry()
    tel.track("demo", "pick_shown", {"arm": "sie", "count": 20})
    tel.track("u2", "pick_shown", {"arm": "tfidf", "event_ids": [str(i) for i in range(40)]})
    import os
    with open(os.environ["REHEARSALS_PATH"], "w") as f:
        json.dump({"rehearsals": [{"name": "Simulated Luma", "passed": 9, "total": 10},
                                  {"name": "Paid event", "value": "Asked first, as it should", "ok": True}]}, f)
    ops = c.OpsSummary.model_validate(env.client.get("/api/ops").json())
    assert ops.sample is False
    assert {a.arm: a.rsvp_rate for a in ops.arms} == {"sie": pytest.approx(3 / 20), "tfidf": pytest.approx(1 / 40)}
    assert ops.rehearsals[0] == c.Rehearsal(name="Simulated Luma", value="9 of 10", ok=True)

    sample = c.OpsSummary.model_validate(env.client.get("/api/ops?sample=1").json())
    assert sample.sample and sample.runs_today == 128 and sample.recent[0].run == "RSVP · Agent builders night"
