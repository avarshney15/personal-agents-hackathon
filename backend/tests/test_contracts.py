"""Lead smoke test: every fake satisfies its interface."""

from app import contracts as c
from app.act.fake import FakeRsvpAgent
from app.catalog.fake import FakeCatalog
from app.demo import demo_user, seed_demo_memory
from app.sponsors.identity.fake import FakeIdentity
from app.sponsors.memory.fake import FakeMemory
from app.sponsors.ranking.fake import FakeRanking
from app.sponsors.telemetry.fake import FakeTelemetry


def test_fakes_match_interfaces():
    assert isinstance(FakeMemory(), c.Memory)
    assert isinstance(FakeRanking(), c.Ranking)
    assert isinstance(FakeIdentity(), c.Identity)
    assert isinstance(FakeTelemetry(), c.Telemetry)
    assert isinstance(FakeCatalog(), c.Catalog)
    assert isinstance(FakeRsvpAgent(), c.RsvpAgent)


def test_demo_memory_and_recall():
    memory = FakeMemory()
    seed_demo_memory(memory)
    assert len(memory.facts(c.DEMO_USER_ID).went) == 2
    event = FakeCatalog().get("fake-agent-builders")
    assert memory.recall(c.DEMO_USER_ID, event)


def test_fake_rsvp_walks_five_steps(monkeypatch):
    monkeypatch.setenv("FAKE_STEP_SECONDS", "0")
    seen = []
    result = FakeRsvpAgent().run("r1", demo_user(), FakeCatalog().events()[0], "hi",
                                 seen.append, lambda _: None)
    assert result.status == "done"
    assert [s.key for s in seen if s.state == "done"] == c.STEP_ORDER


def test_calendar_fake_and_new_fields():
    from datetime import datetime, timedelta, timezone

    from app.extra.calendar_fake import FakeCalendar

    cal = FakeCalendar()
    assert isinstance(cal, c.Calendar)
    now = datetime.now(timezone.utc)
    assert cal.items("demo", now, now + timedelta(days=8))
    assert c.User(id="u", name="n").spend_limit_usd == 2.0
    assert c.RsvpResult(status="done").cost_usd == 0.0
