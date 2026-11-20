"""API tests on the fakes (Part 5). Run: FAKE_STEP_SECONDS=0 .venv/bin/python -m pytest -q tests/test_api.py"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import registry, store, worker
from app.contracts import STEP_ORDER, STEP_TEXT, RsvpResult, RsvpStep, RunNeed

_CACHED = (store.get_store, registry.get_memory, registry.get_ranking, registry.get_identity,
           registry.get_telemetry, registry.get_catalog, registry.get_calendar, registry.get_rsvp_agent)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _drain() -> None:
    for future in list(worker._futures.values()):
        future.result(timeout=10)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FAKE_STEP_SECONDS", "0")
    monkeypatch.setenv("SPONSORS_FAKE", "1")
    monkeypatch.setenv("ACT_FAKE", "1")
    for fn in _CACHED:
        fn.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        yield c
    _drain()
    for fn in _CACHED:
        fn.cache_clear()


def _verify(client, headers=None) -> None:
    client.post("/api/verify/start", headers=headers or {})
    assert client.get("/api/verify/status", headers=headers or {}).json() == {"verified": True}


def _event_id() -> str:
    return registry.get_catalog().events()[0].id


def _poll(client, run_id: str, until=("done", "needs_you", "failed"), timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/rsvp/{run_id}").json()
        if run["status"] in until:
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} stuck at {run['status']}")


def _tracked(name: str) -> list[dict]:
    return [props for _uid, event, props in registry.get_telemetry().events if event == name]


class _Agent:
    """Scriptable stand-in for RsvpAgent with the new ask_user callback."""

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def run(self, run_id, user, event, disclosure, on_step, on_frame, ask_user=None):
        return self.behaviour(run_id, on_step, on_frame, ask_user)


def _step(run_id, key, state):
    title, detail = STEP_TEXT[key]
    return RsvpStep(run_id=run_id, key=key, title=title, detail=detail, state=state)


# ---------------------------------------------------------------- basics


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_me_is_demo_user(client):
    me = client.get("/api/me").json()
    assert me["id"] == "demo" and me["verified"] is False and me["spend_limit_usd"] == 2.0


def test_x_user_id_creates_eval_user(client):
    me = client.get("/api/me", headers={"X-User-Id": "e1"}).json()
    assert me == {**me, "id": "e1", "name": "Eval user e1", "verified": False}
    _verify(client, {"X-User-Id": "e1"})
    assert client.get("/api/me", headers={"X-User-Id": "e1"}).json()["verified"] is True
    assert client.get("/api/me").json()["verified"] is False  # demo user untouched


def test_cookie_user_wins_over_header(client):
    client.cookies.set("ea_user", "maya@example.com")
    me = client.get("/api/me", headers={"X-User-Id": "e2"}).json()
    client.cookies.clear()
    assert me["id"] == "maya@example.com" and me["email"] == "maya@example.com"


def test_cors_allows_web_app(client):
    resp = client.options("/api/me", headers={"Origin": "http://localhost:5173",
                                              "Access-Control-Request-Method": "GET"})
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------------------------------------------------------------- verify


def test_verify_start_and_status(client):
    session = client.post("/api/verify/start").json()
    assert session["inquiry_id"]
    assert client.get("/api/me").json()["persona_inquiry_id"] == session["inquiry_id"]
    assert client.get("/api/verify/status").json() == {"verified": True}
    me = client.get("/api/me").json()
    assert me["verified"] is True and me["verified_at"]
    assert _tracked("verify_started") and _tracked("verify_passed")


def test_verify_webhook_marks_user(client):
    client.get("/api/me", headers={"X-User-Id": "hook"})
    assert client.post("/api/verify/webhook", json={"user_id": "hook"}).json() == {"ok": True}
    assert client.get("/api/me", headers={"X-User-Id": "hook"}).json()["verified"] is True
    assert client.post("/api/verify/webhook", json={"nothing": 1}).json() == {"ok": True}


# ---------------------------------------------------------------- events and RSVP


def test_get_event(client):
    event_id = _event_id()
    assert client.get(f"/api/events/{event_id}").json()["id"] == event_id
    assert client.get("/api/events/nope").status_code == 404


def test_rsvp_needs_verified_user(client):
    resp = client.post("/api/rsvp", json={"event_id": _event_id()})
    assert resp.status_code == 403
    assert "Verify" in resp.json()["detail"]


def test_rsvp_unknown_event(client):
    _verify(client)
    assert client.post("/api/rsvp", json={"event_id": "nope"}).status_code == 404


def test_rsvp_runs_queued_to_done(client):
    _verify(client)
    event_id = _event_id()
    run = client.post("/api/rsvp", json={"event_id": event_id}).json()
    assert run["status"] == "queued" and run["event_id"] == event_id
    assert [s["key"] for s in run["steps"]] == STEP_ORDER
    assert {s["state"] for s in run["steps"]} == {"waiting"}

    done = _poll(client, run["id"])
    assert done["status"] == "done" and done["outcome"] == "going" and done["ended_at"]
    assert [s["state"] for s in done["steps"]] == ["done"] * 5
    assert "verified person" in done["steps"][2]["detail"]  # the disclosure, from Identity
    assert done["arm"] in ("sie", "tfidf")
    assert client.get(f"/api/rsvp/{run['id']}/frame").status_code == 404  # fake sends no frames

    assert _tracked("rsvp_started") and _tracked("rsvp_done")
    assert len(_tracked("rsvp_step")) == 10
    went = client.get("/api/memory").json()["went"]
    assert any(f["text"].startswith("RSVP'd to") and f["event_id"] == event_id for f in went)


def test_rsvp_unknown_run(client):
    assert client.get("/api/rsvp/run_nope").status_code == 404
    assert client.get("/api/rsvp/run_nope/frame").status_code == 404


def test_rsvp_saves_frames_and_result(client, monkeypatch):
    def behaviour(run_id, on_step, on_frame, ask_user):
        on_frame(PNG)
        for key in STEP_ORDER:
            on_step(_step(run_id, key, "done"))
        return RsvpResult(status="done", outcome="waitlist", cost_usd=0.12, model="claude-test")

    monkeypatch.setattr(registry, "get_rsvp_agent", lambda: _Agent(behaviour))
    _verify(client)
    run = _poll(client, client.post("/api/rsvp", json={"event_id": _event_id()}).json()["id"])
    assert run["has_frame"] is True and run["outcome"] == "waitlist"
    assert run["cost_usd"] == pytest.approx(0.12) and run["model"] == "claude-test"
    frame = client.get(f"/api/rsvp/{run['id']}/frame")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/png"
    assert frame.content == PNG


def test_rsvp_crash_marks_failed(client, monkeypatch):
    def behaviour(run_id, on_step, on_frame, ask_user):
        on_step(_step(run_id, "checked", "current"))
        raise RuntimeError("Luma changed its button")

    monkeypatch.setattr(registry, "get_rsvp_agent", lambda: _Agent(behaviour))
    _verify(client)
    run = _poll(client, client.post("/api/rsvp", json={"event_id": _event_id()}).json()["id"])
    assert run["status"] == "failed" and "Luma changed its button" in run["stop_reason"]
    assert run["steps"][0]["state"] == "failed"
    assert _tracked("rsvp_needs_you")


def test_rsvp_ask_user_then_answer(client, monkeypatch):
    got = {}

    def behaviour(run_id, on_step, on_frame, ask_user):
        on_step(_step(run_id, "opened", "current"))
        got["value"] = ask_user(RunNeed(kind="code", title="Enter the code Luma emailed you",
                                        detail="Your Luma login expired.", step_key="opened",
                                        input_label="Six-digit code"))
        for key in STEP_ORDER:
            on_step(_step(run_id, key, "done"))
        return RsvpResult(status="done")

    monkeypatch.setattr(registry, "get_rsvp_agent", lambda: _Agent(behaviour))
    _verify(client)
    run = client.post("/api/rsvp", json={"event_id": _event_id()}).json()
    waiting = _poll(client, run["id"], until=("needs_you",))
    assert waiting["need"]["kind"] == "code" and waiting["need"]["expires_at"]
    assert waiting["ended_at"] is None

    answered = client.post(f"/api/rsvp/{run['id']}/answer", json={"value": "123456"})
    assert answered.status_code == 200 and answered.json()["status"] == "running"
    done = _poll(client, run["id"])
    assert done["status"] == "done" and done["need"] is None
    assert got["value"] == "123456"
    assert client.post(f"/api/rsvp/{run['id']}/answer", json={"value": "1"}).status_code == 409


def test_rsvp_cancel_while_waiting(client, monkeypatch):
    got = {}

    def behaviour(run_id, on_step, on_frame, ask_user):
        got["value"] = ask_user(RunNeed(kind="choice", title="Pick a ticket", detail="Two kinds.",
                                        step_key="register"))
        return RsvpResult(status="needs_you", stop_reason="No answer")

    monkeypatch.setattr(registry, "get_rsvp_agent", lambda: _Agent(behaviour))
    _verify(client)
    run = client.post("/api/rsvp", json={"event_id": _event_id()}).json()
    _poll(client, run["id"], until=("needs_you",))
    cancelled = client.post(f"/api/rsvp/{run['id']}/cancel").json()
    assert cancelled["status"] == "failed" and cancelled["stop_reason"] == worker.CANCELLED_REASON
    _drain()
    assert got["value"] is None
    assert client.get(f"/api/rsvp/{run['id']}").json()["status"] == "failed"  # agent's result ignored


def test_spend_limit_stops_run(client):
    _verify(client)
    assert client.patch("/api/me", json={"spend_limit_usd": 0}).json()["spend_limit_usd"] == 0
    run = _poll(client, client.post("/api/rsvp", json={"event_id": _event_id()}).json()["id"])
    assert run["status"] == "needs_you" and run["need"]["kind"] == "spend_limit"
    assert _tracked("spend_limit_hit")


def test_store_list_runs_and_recovery(client):
    s = store.get_store()
    run = s.create_run("demo", _event_id())
    s.set_status(run.id, "running")
    assert [r.id for r in store.list_runs("demo")] == [run.id]
    assert store.list_runs("someone-else") == []
    assert worker.recover_on_startup() == 1
    assert store.get_run(run.id).status == "failed"


# ---------------------------------------------------------------- me


def test_patch_me(client):
    me = client.patch("/api/me", json={"ask_before": "always", "usual_times": ["Weekend days"],
                                       "name": "Maya"}).json()
    assert (me["ask_before"], me["usual_times"], me["name"]) == ("always", ["Weekend days"], "Maya")
    assert client.get("/api/me").json()["name"] == "Maya"
    assert client.patch("/api/me", json={"ask_before": "sometimes"}).status_code == 422


def test_delete_me_forgets_everything(client):
    _verify(client, {"X-User-Id": "gone"})
    client.post("/api/verify/start", headers={"X-User-Id": "gone"})
    registry.get_memory().remember("gone", "said", "Likes jazz", "You told the agent")
    assert client.delete("/api/me", headers={"X-User-Id": "gone"}).json() == {"ok": True}
    assert registry.get_memory().facts("gone").said == []
    assert client.get("/api/me", headers={"X-User-Id": "gone"}).json()["verified"] is False


# ---------------------------------------------------------------- memory


def test_memory_forget_and_forget_all(client):
    groups = client.get("/api/memory").json()
    assert len(groups["said"]) == 1 and len(groups["went"]) == 2 and len(groups["skipped"]) == 1
    fact_id = groups["went"][0]["id"]
    after = client.delete(f"/api/memory/{fact_id}").json()
    assert fact_id not in [f["id"] for f in after["went"]] and len(after["went"]) == 1
    empty = client.delete("/api/memory").json()
    assert empty == {"said": [], "went": [], "skipped": []}
    assert len(_tracked("memory_forgotten")) == 2


# ---------------------------------------------------------------- For you (needs Part 2's app.feed)


def test_feed_and_into(client):
    pytest.importorskip("app.feed")
    feed = client.get("/api/feed").json()
    assert set(feed) >= {"picks", "into", "arm"}
    after = client.post("/api/into", json={"text": "climbing and AI agents"}).json()
    chips = client.get("/api/me").json()["into"]
    assert chips and after["into"] == chips
    tag = chips[0]
    gone = client.delete(f"/api/into/{tag}").json()
    assert tag not in client.get("/api/me").json()["into"]
    assert tag not in gone["into"]
    assert client.post("/api/into", json={"text": "  "}).status_code == 422
