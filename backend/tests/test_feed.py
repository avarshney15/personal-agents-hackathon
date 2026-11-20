"""Part 2: For you, Ask, and the Event sheet on fakes, plus the Claude text path (mocked)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

from app import contracts as c
from app import feed, registry
from app.catalog import text as text_jobs
from app.catalog.fake import FakeCatalog
from app.demo import demo_user, seed_demo_memory
from app.sponsors.memory.fake import FakeMemory
from app.sponsors.ranking.fake import FakeRanking
from app.sponsors.ranking.sie_ranking import SieClient, SieRanking
from app.sponsors.telemetry.fake import FakeTelemetry
from tests.test_ranking import FakeSie

PT = ZoneInfo("America/Los_Angeles")


class CountingMemory(FakeMemory):
    def __init__(self) -> None:
        super().__init__()
        self.recalls = 0

    def recall(self, user_id, event, k=3):
        self.recalls += 1
        return super().recall(user_id, event, k)


class FixedArm(FakeTelemetry):
    def __init__(self, arm: c.Arm) -> None:
        super().__init__()
        self.fixed = arm

    def arm(self, user_id, experiment="ranking"):
        return self.fixed

    def count(self, name: str) -> int:
        return sum(1 for _, event, _ in self.events if event == name)


class ListCatalog:
    def __init__(self, events: list[c.Event]) -> None:
        self._events = events

    def events(self):
        return list(self._events)

    def get(self, event_id):
        return next((e for e in self._events if e.id == event_id), None)


def make_ranking(kind: str):
    if kind == "fake":
        return FakeRanking(), "sie"
    if kind == "tfidf":
        return SieRanking(client=SieClient(api_key="", base_url=""), cache_path=""), "tfidf"
    return SieRanking(client=FakeSie().client(), cache_path=""), "sie"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TEXT_PROVIDER", raising=False)
    w = SimpleNamespace(memory=CountingMemory(), telemetry=FixedArm("sie"), ranking=FakeRanking(),
                        catalog=FakeCatalog())
    seed_demo_memory(w.memory)
    monkeypatch.setattr(registry, "get_memory", lambda: w.memory)
    monkeypatch.setattr(registry, "get_telemetry", lambda: w.telemetry)
    monkeypatch.setattr(registry, "get_ranking", lambda: w.ranking)
    monkeypatch.setattr(registry, "get_catalog", lambda: w.catalog)

    def use(kind: str) -> None:
        w.ranking, w.telemetry.fixed = make_ranking(kind)

    w.use = use
    return w


def fact_by_id(memory: FakeMemory, user_id: str, fact_id: str) -> c.Fact:
    groups = memory.facts(user_id)
    return next(f for f in [*groups.said, *groups.went, *groups.skipped] if f.id == fact_id)


def quotes(why: str, fact: c.Fact) -> bool:
    core = fact.text.split(" ", 1)[1] if " " in fact.text else fact.text
    return core.lower() in why.lower()


def order(resp) -> list[str]:
    return [p.event.id for p in resp.picks]


# ---------------------------------------------------------------- For you


@pytest.mark.parametrize("kind", ["fake", "tfidf", "sie"])
def test_saying_climbing_and_agents_reorders_for_you(world, kind):
    world.use(kind)
    user = demo_user()
    before = order(feed.build_feed(user))
    assert before.index("fake-bouldering") > before.index("fake-agent-builders")

    user.into = feed.add_into(user, "climbing and AI agents lately")
    assert user.into == ["Climbing", "AI agents"]
    resp = feed.build_feed(user)
    after = order(resp)
    assert after.index("fake-bouldering") < after.index("fake-ceramics")
    assert after.index("fake-agent-builders") < after.index("fake-ceramics")
    assert after.index("fake-evals-workshop") < after.index("fake-ceramics")
    assert after.index("fake-bouldering") < before.index("fake-bouldering") or before[0] == "fake-bouldering"

    assert resp.arm == world.telemetry.fixed and all(p.arm == resp.arm for p in resp.picks)
    assert resp.into == ["Climbing", "AI agents"]
    picks = {p.event.id: p for p in resp.picks}
    assert picks["fake-agent-builders"].tag == "Strong match"
    assert picks["fake-ceramics"].tag == "Something new"
    assert picks["fake-ceramics"].why.startswith("Something new: ")
    assert all(p.score >= 0 for p in resp.picks)


def test_why_lines_quote_real_facts(world):
    user = demo_user()
    user.into = feed.add_into(user, "climbing and AI agents lately")
    resp = feed.build_feed(user)
    strong = [p for p in resp.picks if p.tag == "Strong match"]
    assert len(strong) >= 2
    for pick in resp.picks:
        if pick.why_fact_id:
            assert quotes(pick.why, fact_by_id(world.memory, user.id, pick.why_fact_id)), pick.why
    for pick in strong:
        assert pick.why_fact_id
    bouldering = next(p for p in resp.picks if p.event.id == "fake-bouldering")
    assert bouldering.why == "You said climbing and AI agents lately."
    assert len({p.why for p in strong}) == len(strong)  # different facts for different picks


def test_feed_tracks_pick_shown_once_and_into_added(world):
    user = demo_user()
    feed.build_feed(user)
    assert world.telemetry.count("pick_shown") == 1
    feed.add_into(user, "climbing and AI agents lately")
    assert world.telemetry.count("into_added") == 1
    props = next(p for _, e, p in world.telemetry.events if e == "into_added")
    assert props["tags"] == ["Climbing", "AI agents"]
    feed.build_feed(user, limit=2)
    assert world.telemetry.count("pick_shown") == 2
    assert len(feed.build_feed(user, limit=2).picks) == 2


def test_add_into_remembers_and_merges_chips(world):
    user = demo_user()
    user.into = ["Climbing"]
    assert feed.add_into(user, "AI agents and climbing") == ["AI agents", "Climbing"]
    said = world.memory.facts(user.id).said
    fact = next(f for f in said if f.text == "Into AI agents and climbing lately")
    assert re.fullmatch(r"You told the agent, [A-Z][a-z]{2} \d{1,2}", fact.from_text)

    feed.add_into(user, "Pottery. Not big networking mixers.")
    skipped = [f.text for f in world.memory.facts(user.id).skipped]
    assert "Not into big networking mixers" in skipped

    count = len(world.telemetry.events)
    assert feed.add_into(user, "   ") == user.into
    assert len(world.telemetry.events) == count


def test_recall_is_cached_until_memory_changes(world):
    user = demo_user()
    feed.build_feed(user)
    first = world.memory.recalls
    assert first > 0
    feed.build_feed(user)
    assert world.memory.recalls == first
    feed.add_into(user, "climbing")
    feed.build_feed(user)
    assert world.memory.recalls > first


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=PT)


def _next_monday() -> date:
    today = datetime.now(PT).date()
    return today + timedelta(days=7 - today.weekday())


def _ev(event_id: str, title: str, when: datetime, going: Optional[int] = None, desc: str = "",
        tags=(), hood: str = "SoMa") -> c.Event:
    return c.Event(id=event_id, title=title, starts_at=when, url="https://lu.ma/x", description=desc,
                   tags=list(tags), going_count=going, neighborhood=hood)


def test_usual_times_boost(world):
    mon = _next_monday()
    world.catalog = ListCatalog([
        _ev("weeknight", "Board games", _at(mon + timedelta(days=1), 19)),
        _ev("saturday", "Park picnic", _at(mon + timedelta(days=5), 13)),
    ])
    user = c.User(id="new-user", name="New")
    user.usual_times = ["Weekend days"]
    resp = feed.build_feed(user)
    assert order(resp)[0] == "saturday"
    assert resp.picks[0].why == "Something new: a weekend afternoon in SoMa, when you usually go."
    user.usual_times = ["Weekday evenings"]
    assert order(feed.build_feed(user))[0] == "weeknight"


# ---------------------------------------------------------------- Ask


def _ask_catalog() -> ListCatalog:
    mon = _next_monday()
    d = lambda n: mon + timedelta(days=n)  # noqa: E731
    hack = ["AI agents", "hackathon"]
    return ListCatalog([
        _ev("browser", "Browser agents build night", _at(d(2), 18), 80,
            "Hands-on agent hackathon evening. Build browser agents.", hack, "Mission"),
        _ev("sprint", "Weekend agent sprint", _at(d(5), 10), 150,
            "Hands-on agent hackathon in teams of 2 to 4.", hack),
        _ev("voice", "Voice agents hack evening", _at(d(3), 17, 30), 120,
            "An agent hackathon for voice agents.", hack, "FiDi"),
        _ev("huge", "AI agents mega hackathon", _at(d(5), 11), 900, "The big agent hackathon.", hack),
        _ev("summit", "Agent hackathon summit", _at(d(4), 18), 1200, "Agent hackathon and talks.", hack),
        _ev("breakfast", "Agent hackathon breakfast", _at(d(1), 8), 40, "Agent hackathon, early.", hack),
        _ev("boulder", "Bouldering social", _at(d(2), 19), 60, "Climbing meetup.", ["climbing"], "Dogpatch"),
        _ev("later", "Agent hackathon redux", _at(d(9), 18), 50, "Agent hackathon.", hack),
    ])


@pytest.mark.parametrize("kind", ["fake", "tfidf"])
def test_ask_filters_ranks_and_explains(world, kind):
    world.use(kind)
    world.catalog = _ask_catalog()
    user = demo_user()
    resp = feed.ask(user, "Agent hackathons next week, evenings or weekend, not too big")
    assert resp.query == "Agent hackathons next week, evenings or weekend, not too big"
    assert resp.filters == "Next week · evenings and weekend · under 300"
    assert resp.summary == "Three fit. I left out two that are over 300 people, since you said not too big."
    assert set(order(resp)) == {"browser", "sprint", "voice"}
    assert {p.tag for p in resp.picks} <= {"Strong match", "Match"}
    assert all(p.why for p in resp.picks)
    assert any(p.why_fact_id for p in resp.picks)
    for pick in resp.picks:
        if pick.why_fact_id:
            assert quotes(pick.why, fact_by_id(world.memory, user.id, pick.why_fact_id))
    assert world.telemetry.count("ask_query") == 1
    props = next(p for _, e, p in world.telemetry.events if e == "ask_query")
    assert props["results"] == 3 and props["left_out"] == 2


def test_ask_with_nothing_fitting(world):
    world.catalog = _ask_catalog()
    resp = feed.ask(demo_user(), "pottery classes next week")
    assert resp.picks == [] and resp.summary == "Nothing fits that yet."


# ---------------------------------------------------------------- Event sheet


def test_event_why_quotes_memory(world):
    user = demo_user()
    user.into = feed.add_into(user, "climbing and AI agents lately")
    event = world.catalog.get("fake-agent-builders")
    why, facts, tag = feed.event_why(user, event)
    assert tag == "Strong match"
    assert 2 <= why.count(".") <= 4 and len(re.findall(r"[.!?](?:\s|$)", why)) in (2, 3)
    assert facts and all(quotes(why, f) for f in facts)
    assert "This one is hands-on" in why


def test_event_why_never_contradicts_a_time_fact(world):
    mon = _next_monday()
    saturday = _ev("sat-ceramics", "Ceramics for beginners", _at(mon + timedelta(days=5), 14),
                   desc="A weekend afternoon at the wheel.", tags=["art", "weekend"])
    wednesday = _ev("wed-ceramics", "Ceramics for beginners", _at(mon + timedelta(days=2), 14),
                    desc="A weekend afternoon at the wheel.", tags=["art", "weekend"])
    world.catalog = ListCatalog([saturday, wednesday])
    user = demo_user()
    sat_why, sat_facts, _ = feed.event_why(user, saturday)
    wed_why, wed_facts, _ = feed.event_why(user, wednesday)
    assert "Weekend afternoons stay free" in sat_why and sat_facts
    assert "Weekend afternoons" not in wed_why and not wed_facts
    picks = {p.event.id: p for p in feed.build_feed(user).picks}
    assert "Weekend afternoons stay free" in picks["sat-ceramics"].why
    assert "Weekend afternoons" not in picks["wed-ceramics"].why


# ---------------------------------------------------------------- Claude text path (mocked)


class FakeMessages:
    def __init__(self, payload=None, stop_reason="end_turn", error: Optional[Exception] = None) -> None:
        self.payload, self.stop_reason, self.error = payload, stop_reason, error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(stop_reason=self.stop_reason, content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=json.dumps(self.payload)),
        ])


@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("TEXT_MODEL", raising=False)

    def install(**kw) -> FakeMessages:
        messages = FakeMessages(**kw)
        monkeypatch.setattr(text_jobs, "_client", SimpleNamespace(messages=messages))
        return messages

    return install


def test_claude_reads_intent(claude):
    messages = claude(payload={"tags": ["Climbing", "AI agents"], "taste": "climbing; AI agents",
                               "avoid": ["big networking mixers"]})
    intent, avoid = text_jobs.read_taste("Climbing and AI agents lately. Not big networking mixers.")
    assert intent == c.Intent(taste="climbing; AI agents", tags=["Climbing", "AI agents"])
    assert avoid == ["big networking mixers"]
    call = messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["thinking"] == {"type": "adaptive"}
    assert "Climbing and AI agents lately" in call["messages"][0]["content"]


@pytest.mark.parametrize("kw", [{"error": RuntimeError("boom")}, {"payload": {}, "stop_reason": "refusal"},
                                {"payload": {"tags": [], "taste": "", "avoid": []}}])
def test_claude_failures_fall_back_to_keywords(claude, kw):
    claude(**kw)
    assert text_jobs.read_intent("climbing and AI agents lately") == c.Intent(
        taste="climbing; AI agents", tags=["Climbing", "AI agents"])


def test_claude_tags_events_in_one_call(claude):
    events = FakeCatalog().events()
    messages = claude(payload={"events": [{"id": "fake-bouldering", "tags": ["Climbing", "climbing", "Social"]},
                                          {"id": "not-an-event", "tags": ["X"]}]})
    tags = text_jobs.tag_events(events)
    assert len(messages.calls) == 1
    assert tags["fake-bouldering"] == ["Climbing", "Social"]
    assert tags["fake-ceramics"] == ["art", "weekend"]  # missing from the reply: keeps its own tags
    assert "not-an-event" not in tags


def test_claude_reads_ask(claude):
    claude(payload={"topic": "agent hackathons", "date_from": "2026-11-23", "date_to": "2026-11-29",
                    "date_label": "Next week", "times": [{"days": "any", "part": "evening"},
                                                         {"days": "weekend", "part": "any"}],
                    "max_going": 300, "size_phrase": "not too big", "free_only": False})
    q = text_jobs.read_ask("Agent hackathons next week, evenings or weekend, not too big", date(2026, 11, 20))
    assert (q.topic, q.date_from, q.date_to) == ("agent hackathons", date(2026, 11, 23), date(2026, 11, 29))
    assert q.filters == "Next week · evenings and weekend · under 300"


def test_claude_client_is_wrapped_by_telemetry(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(text_jobs, "_client", None)
    wrapped = []

    class Recording(FakeTelemetry):
        def wrap(self, client):
            wrapped.append(client)
            return client

    monkeypatch.setattr(registry, "get_telemetry", lambda: Recording())
    client = text_jobs._claude()
    assert wrapped == [client] and type(client).__name__ == "Anthropic"
    monkeypatch.setattr(text_jobs, "_client", None)


def test_arcee_seam_falls_back(monkeypatch):
    monkeypatch.setenv("TEXT_PROVIDER", "arcee")
    monkeypatch.setenv("ARCEE_API_KEY", "x")
    assert text_jobs.provider() == "arcee"
    assert text_jobs.read_intent("jazz").tags == ["Jazz"]
    monkeypatch.delenv("ARCEE_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert text_jobs.provider() == "keywords"
