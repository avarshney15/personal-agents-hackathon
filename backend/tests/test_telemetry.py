"""Part 4: LiveTelemetry with Amplitude and Statsig mocked. No network, no keys."""

from __future__ import annotations

import asyncio
import json
import sys
import threading

import anthropic
import httpx2
import pytest
from amplitude_ai.testing import MockAmplitudeAI

from app import contracts as c
from app.sponsors.telemetry import live
from app.sponsors.telemetry.fake import FakeTelemetry, hash_arm
from app.sponsors.telemetry.live import LiveTelemetry

MESSAGE = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
    "content": [
        {"type": "text", "text": "Clicking Register."},
        {"type": "tool_use", "id": "tu_1", "name": "left_click",
         "input": {"coordinate": [640, 410]}},
    ],
    "stop_reason": "tool_use", "stop_sequence": None,
    "usage": {"input_tokens": 1200, "output_tokens": 40},
}


def claude(status: int = 200, async_: bool = False):
    calls = []

    def handler(request):
        calls.append(request)
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {
                "type": "api_error", "message": "boom"}})
        return httpx2.Response(200, json=MESSAGE)

    transport = httpx2.MockTransport(handler)
    if async_:
        return anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                        http_client=httpx2.AsyncClient(transport=transport)), calls
    return anthropic.Anthropic(api_key="sk-test", max_retries=0,
                               http_client=httpx2.Client(transport=transport)), calls


class FakeExperiment:
    def __init__(self, arm: str = "", group: str | None = None):
        self.arm, self.group_name = arm, group

    def get_string(self, name, fallback):
        return self.arm if name == "arm" and self.arm else fallback


class FakeStatsig:
    def __init__(self, arm: str = "", group: str | None = None, fail: bool = False):
        self.exp = FakeExperiment(arm, group)
        self.fail = fail
        self.exposures: list[tuple[str, str]] = []
        self.logged: list[tuple[str, str, object, dict]] = []
        self.flushed = self.shut = 0

    def get_experiment(self, user, name):
        if self.fail:
            raise RuntimeError("statsig down")
        self.exposures.append((user.user_id, name))
        return self.exp

    def log_event(self, user, event_name, value=None, metadata=None):
        if self.fail:
            raise RuntimeError("statsig down")
        assert all(isinstance(v, str) for v in (metadata or {}).values())
        self.logged.append((user.user_id, event_name, value, metadata))

    def flush_events(self):
        if self.fail:
            raise RuntimeError("statsig down")
        self.flushed += 1
        done = threading.Event()
        done.set()
        return done

    def shutdown(self):
        self.shut += 1
        done = threading.Event()
        done.set()
        return done


def make(statsig=None):
    mock = MockAmplitudeAI()
    tel = LiveTelemetry(amplitude=mock._amplitude, ai=mock, statsig=statsig or FakeStatsig(),
                        register_atexit=False)
    return tel, mock


def props(event):
    return event.event_properties or {}


# ---------------------------------------------------------------- wrap


def test_live_matches_protocol():
    tel, _ = make()
    assert isinstance(tel, c.Telemetry)
    assert isinstance(FakeTelemetry(), c.Telemetry)


def test_wrap_keeps_client_working_and_emits_trace():
    client, calls = claude()
    tel, mock = make()
    wrapped = tel.wrap(client)
    assert isinstance(wrapped, anthropic.Anthropic)

    response = wrapped.messages.create(model="claude-opus-5-5", max_tokens=256,
                                       messages=[{"role": "user", "content": "RSVP please"}])
    assert len(calls) == 1
    assert response.content[1].name == "left_click"
    assert response.usage.input_tokens == 1200

    [trace] = mock.get_events("[Agent] AI Response")
    p = props(trace)
    assert p["[Agent] Model Name"] == "claude-opus-5-5"
    assert p["[Agent] Provider"] == "anthropic"
    assert p["[Agent] Input Tokens"] == 1200
    assert p["[Agent] Output Tokens"] == 40
    assert p["[Agent] Latency Ms"] >= 0
    assert "left_click" in p["[Agent] Tool Calls"]
    assert trace.device_id == live.SERVER_DEVICE_ID


def test_wrap_traces_beta_messages_too():
    client, _ = claude()
    tel, mock = make()
    wrapped = tel.wrap(client)
    response = wrapped.beta.messages.create(model="claude-opus-5-5", max_tokens=256,
                                            messages=[{"role": "user", "content": "go"}])
    assert response.stop_reason == "tool_use"
    [trace] = mock.get_events("[Agent] AI Response")
    p = props(trace)
    assert p["api"] == "beta.messages"
    assert p["[Agent] Input Tokens"] == 1200
    assert "left_click" in p["[Agent] Tool Calls"]


def test_run_groups_calls_into_a_session_with_the_arm():
    client, _ = claude()
    tel, mock = make(FakeStatsig(arm="sie"))
    wrapped = tel.wrap(client)
    assert tel.arm("demo") == "sie"
    with tel.run("demo", "run-123", event_id="evt-1"):
        wrapped.messages.create(model="claude-opus-5-5", max_tokens=64,
                                messages=[{"role": "user", "content": "go"}])
        wrapped.beta.messages.create(model="claude-opus-5-5", max_tokens=64,
                                     messages=[{"role": "user", "content": "go"}])
    traces = mock.get_events("[Agent] AI Response")
    assert len(traces) == 2
    for trace in traces:
        assert trace.user_id == "demo"
        assert props(trace)["[Agent] Session ID"] == "run-123"
        assert "ranking:sie" in props(trace)["[Agent] Experiments"]
    assert mock.get_events("[Agent] Session End")


def test_wrap_error_still_raises_and_is_traced():
    client, _ = claude(status=500)
    tel, mock = make()
    wrapped = tel.wrap(client)
    with pytest.raises(anthropic.APIStatusError):
        wrapped.messages.create(model="claude-opus-5-5", max_tokens=64,
                                messages=[{"role": "user", "content": "go"}])
    with pytest.raises(anthropic.APIStatusError):
        wrapped.beta.messages.create(model="claude-opus-5-5", max_tokens=64,
                                     messages=[{"role": "user", "content": "go"}])
    traces = mock.get_events("[Agent] AI Response")
    assert len(traces) == 2
    assert all(props(t)["[Agent] Is Error"] for t in traces)


def test_wrap_none_and_twice():
    tel, _ = make()
    assert tel.wrap(None) is None
    client, _ = claude()
    once = tel.wrap(client)
    assert tel.wrap(once) is once


def test_wrap_without_amplitude_returns_client_unchanged():
    tel = LiveTelemetry(amplitude_api_key="", statsig_secret="", register_atexit=False)
    client, calls = claude()
    assert tel.wrap(client) is client
    client.messages.create(model="claude-opus-5-5", max_tokens=64,
                           messages=[{"role": "user", "content": "go"}])
    assert len(calls) == 1


def test_wrap_by_hand_when_ai_sdk_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "amplitude_ai", None)  # import amplitude_ai -> ImportError
    sent = []
    amp = MockAmplitudeAI()._amplitude
    amp.track = sent.append
    tel = LiveTelemetry(amplitude=amp, statsig=FakeStatsig(), register_atexit=False)
    client, _ = claude()
    wrapped = tel.wrap(client)
    assert wrapped is client
    with tel.run("demo", "run-9", arm="tfidf"):
        response = wrapped.messages.create(model="claude-opus-5-5", max_tokens=64,
                                           messages=[{"role": "user", "content": "go"}])
    assert response.id == "msg_1"
    [event] = [e for e in sent if e.event_type == "claude_call"]
    p = event.event_properties
    assert event.user_id == "demo"
    assert p["model"] == "claude-opus-5-5" and p["input_tokens"] == 1200
    assert p["output_tokens"] == 40 and p["tool_calls"] == ["left_click"]
    assert p["run_id"] == "run-9" and p["arm"] == "tfidf" and p["latency_ms"] >= 0


def test_wrap_async_client_by_hand():
    tel, mock = make()
    client, _ = claude(async_=True)
    wrapped = tel.wrap(client)
    assert isinstance(wrapped, anthropic.AsyncAnthropic)
    response = asyncio.run(wrapped.messages.create(
        model="claude-opus-5-5", max_tokens=64, messages=[{"role": "user", "content": "go"}]))
    assert response.id == "msg_1"
    [event] = mock.get_events("claude_call")
    assert props(event)["tool_call_count"] == 1


# ---------------------------------------------------------------- track


def test_track_sends_amplitude_and_statsig_with_arm():
    statsig = FakeStatsig(arm="tfidf")
    tel, mock = make(statsig)
    tel.arm("user-7")
    tel.track("user-7", "pick_opened", {"event_id": "evt-1", "rank": 2, "tags": ["AI"]})
    [event] = mock.get_events("pick_opened")
    assert event.user_id == "user-7"
    assert event.event_properties == {"event_id": "evt-1", "rank": 2, "tags": ["AI"],
                                      "arm": "tfidf"}
    [(uid, name, value, meta)] = statsig.logged
    assert (uid, name, value) == ("user-7", "pick_opened", "evt-1")
    assert meta["rank"] == "2" and meta["arm"] == "tfidf"
    identify = mock.get_events("$identify")
    assert identify and identify[0].user_properties["$set"]["ranking_arm"] == "tfidf"


def test_track_never_raises():
    statsig = FakeStatsig(fail=True)
    tel, mock = make(statsig)

    def boom(_event):
        raise RuntimeError("amplitude down")

    mock._amplitude.track = boom
    mock.score = lambda **kw: (_ for _ in ()).throw(RuntimeError("score down"))

    class Weird:
        def __str__(self):
            return "weird"

    tel.track("demo", "rsvp_done", {"run_id": "r1", "when": object(), "x": Weird()})
    tel.track("", "rsvp_started", None)
    tel.flush()
    tel.close()
    assert tel.arm("demo") == hash_arm("demo")


def test_track_rsvp_outcome_scores_the_run():
    tel, mock = make()
    tel.track("demo", "rsvp_done", {"run_id": "run-1", "event_id": "evt-1"})
    tel.track("demo", "rsvp_needs_you", {"run_id": "run-2", "stop_reason": "Host asks questions"})
    scores = mock.get_events("[Agent] Score")
    assert [props(s)["[Agent] Score Value"] for s in scores] == [1.0, 0.0]
    assert props(scores[0])["[Agent] Session ID"] == "run-1"


# ---------------------------------------------------------------- arm


def test_arm_from_statsig_parameter_is_cached():
    statsig = FakeStatsig(arm="sie")
    tel, _ = make(statsig)
    assert tel.arm("u1") == "sie"
    assert tel.arm("u1") == "sie"
    assert statsig.exposures == [("u1", "ranking")]


@pytest.mark.parametrize("group,expected", [("Control", "tfidf"), ("Test", "sie"),
                                            ("sie", "sie"), ("tfidf", "tfidf")])
def test_arm_from_group_name(group, expected):
    tel, _ = make(FakeStatsig(group=group))
    assert tel.arm("u2") == expected


def test_arm_falls_back_when_statsig_fails():
    tel, _ = make(FakeStatsig(fail=True))
    for uid in ("a-user", "b-user", "c-user", "demo"):
        assert tel.arm(uid) == hash_arm(uid)


def test_arm_falls_back_when_experiment_has_no_values():
    tel, _ = make(FakeStatsig(arm="", group=None))  # what Statsig returns with a bad key
    assert tel.arm("demo") == hash_arm("demo")


def test_arm_without_keys_uses_hash():
    tel = LiveTelemetry(amplitude_api_key="", statsig_secret="", register_atexit=False)
    assert tel.arm("demo") in ("tfidf", "sie")
    assert tel.arm("demo") == hash_arm("demo")
    tel.track("demo", "pick_shown", {"event_id": "e"})
    tel.flush()


def test_hash_split_is_roughly_even():
    arms = [hash_arm(f"eval-user-{i}") for i in range(200)]
    assert 60 < arms.count("sie") < 140


def test_flush_and_close():
    statsig = FakeStatsig()
    tel, _ = make(statsig)
    tel.flush()
    assert statsig.flushed == 1
    tel.close()
    tel.close()
    assert statsig.shut == 1


def test_props_are_made_json_safe():
    from datetime import datetime

    tel, mock = make()
    tel.track("demo", "memory_forgotten", {"at": datetime(2026, 11, 20, 13, 0), "ids": {"a", }})
    [event] = mock.get_events("memory_forgotten")
    json.dumps(event.event_properties)
    assert event.event_properties["at"] == "2026-11-20T13:00:00"


# ---------------------------------------------------------------- evals/run.py

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402

_EVALS = Path(__file__).resolve().parents[2] / "evals" / "run.py"
_spec = importlib.util.spec_from_file_location("evals_run", _EVALS)
evals_run = importlib.util.module_from_spec(_spec)
sys.modules["evals_run"] = evals_run  # dataclasses look the module up
_spec.loader.exec_module(evals_run)

CATALOG = {
    "e-agents": {"id": "e-agents", "title": "AI Agents Hack Night", "tags": ["AI agents"]},
    "e-climb": {"id": "e-climb", "title": "Bouldering social", "tags": ["Climbing"]},
    "e-jazz": {"id": "e-jazz", "title": "Jazz at the Royal Cuckoo", "tags": ["Music"]},
    "e-mixer": {"id": "e-mixer", "title": "Sales mixer", "tags": ["Networking"]},
    "e-askme": {"id": "e-askme", "title": "Founder dinner (host asks questions)", "tags": []},
}


class FakeApi:
    """In-memory stand-in for the Part 5 routes, enough to drive evals/run.py."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.runs: dict[str, dict] = {}
        self.forgotten: list[tuple[str, str]] = []
        self.cancelled: list[str] = []
        self.verified: set[str] = set()
        self.facts: dict[str, list[dict]] = {}

    def picks(self, user, into):
        arm = hash_arm(user)
        order = ["e-mixer", "e-agents", "e-climb", "e-jazz", "e-askme"]
        if arm == "sie":  # the "better" arm puts the persona's fit first
            order.sort(key=lambda e: 0 if evals_run.fits({"event": CATALOG[e]}, into) else 1)
        return {"arm": arm, "into": [], "picks": [
            {"event": CATALOG[e], "score": 1.0, "why": "", "tag": "Strong match", "arm": arm}
            for e in order]}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        user = request.headers.get("x-user-id")
        path, method = request.url.path, request.method
        body = json.loads(request.content or b"{}") if request.content else {}
        words = next((w for t, w in evals_run.PERSONAS
                      if t == (self.facts.get(user) or [{}])[0].get("text")), [])
        if path == "/api/me":
            return httpx.Response(200, json={"id": user, "name": user})
        if path == "/api/verify/start":
            self.verified.add(user)
            return httpx.Response(200, json={"inquiry_id": "inq"})
        if path == "/api/verify/status":
            return httpx.Response(200, json={"verified": user in self.verified})
        if path == "/api/feed":
            return httpx.Response(200, json=self.picks(user, words))
        if path == "/api/into":
            self.facts[user] = [{"id": f"f-{user}", "text": body["text"]}]
            words = next(w for t, w in evals_run.PERSONAS if t == body["text"])
            return httpx.Response(200, json=self.picks(user, words))
        if path.startswith("/api/events/"):
            return httpx.Response(200, json=CATALOG[path.rsplit("/", 1)[1]])
        if path == "/api/rsvp" and method == "POST":
            if user not in self.verified:
                return httpx.Response(403, json={"detail": "verify"})
            run_id = f"run-{len(self.runs) + 1}"
            self.runs[run_id] = {"id": run_id, "event_id": body["event_id"], "status": "queued",
                                 "polls": 0, "ended_at": None, "need": None, "cost_usd": 0.02,
                                 "model": "claude-opus-5-5"}
            return httpx.Response(200, json=self.runs[run_id])
        if path.endswith("/cancel"):
            run = self.runs[path.split("/")[3]]
            self.cancelled.append(run["id"])
            run.update(status="needs_you", ended_at="now", stop_reason="cancelled")
            return httpx.Response(200, json=run)
        if path.startswith("/api/rsvp/"):
            run = self.runs[path.rsplit("/", 1)[1]]
            run["polls"] += 1
            if run["polls"] == 1:
                run["status"] = "running"
            elif run["event_id"] == "e-askme":
                run.update(status="needs_you", need={"kind": "code"})
            else:
                run.update(status="done", ended_at="now")
            return httpx.Response(200, json=run)
        if path == "/api/memory":
            return httpx.Response(200, json={"said": self.facts.get(user, []), "went": [],
                                             "skipped": []})
        if path.startswith("/api/memory/") and method == "DELETE":
            self.forgotten.append((user, path.rsplit("/", 1)[1]))
            return httpx.Response(200, json={"said": [], "went": [], "skipped": []})
        return httpx.Response(404, json={"detail": "Not Found"})


def test_evals_replays_the_demo_against_a_mock_api(capsys):
    api = FakeApi()
    rows = evals_run.main(["--users", "12", "--tag", "t", "--concurrency", "3", "--poll", "0"],
                          transport=httpx.MockTransport(api), sleep=lambda _s: None)
    by_arm = {r["arm"]: r for r in rows}
    total = by_arm["all"]
    assert total["users"] == 12
    assert by_arm["sie"]["users"] + by_arm["tfidf"]["users"] == 12
    assert total["runs"] == total["done"] + total["needs_you"] + total["failed"] + total["other"]
    assert total["runs"] >= 1 and total["done"] >= 1
    assert total["errors"] == 0
    # every request carries the synthetic user
    assert all(r.headers.get("x-user-id", "").startswith("eval-t-") for r in api.requests)
    assert len({r.headers["x-user-id"] for r in api.requests}) == 12
    # each user forgot the fact they added
    assert sorted(u for u, _ in api.forgotten) == sorted(f"eval-t-{i:03d}" for i in range(1, 13))
    out = capsys.readouterr().out
    assert "needs_you" in out and "Not mounted yet, skipped: /api/activity" in out


def test_evals_cancels_runs_waiting_on_a_person():
    api = FakeApi()
    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(api))
    persona = ("Founder dinners and startup demos", ["founder"])
    result = evals_run.run_user(client, "eval-x-001", persona,
                                evals_run.Options(poll_every=0, extras=False),
                                sleep=lambda _s: None)
    assert result.opened == "e-askme" and result.hit_rank is not None
    assert result.status == "needs_you" and api.cancelled == [result.run_id]
    rows = evals_run.summarize([result])
    assert rows[-1]["needs_you"] == 1


def test_evals_survives_a_dead_api():
    def down(_request):
        raise httpx.ConnectError("refused")

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(down))
    result = evals_run.run_user(client, "eval-y-001", evals_run.PERSONAS[0], evals_run.Options())
    assert result.status is None and result.errors
    assert evals_run.format_table(evals_run.summarize([result]))


def test_evals_unverified_rsvp_is_counted_not_crashed():
    api = FakeApi()
    api.verified = set()
    api_call = api.__call__

    def no_verify(request):
        if request.url.path == "/api/verify/start":
            return httpx.Response(200, json={"inquiry_id": "inq"})
        return api_call(request)

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(no_verify))
    persona = ("AI agents and LLM hackathons", ["agent", "ai"])
    result = evals_run.run_user(client, "eval-z-001", persona,
                                evals_run.Options(poll_every=0, extras=False))
    assert result.status == "unverified" and not result.verified


def test_evals_writes_rehearsals_for_ops(tmp_path):
    from app.contracts import Rehearsal

    out = tmp_path / "rehearsals.json"
    evals_run.main(["--users", "8", "--tag", "r", "--poll", "0", "--no-extras",
                    "--rehearsals", str(out)],
                   transport=httpx.MockTransport(FakeApi()), sleep=lambda _s: None)
    rows = [Rehearsal(**r) for r in json.loads(out.read_text())]
    assert rows and rows[0].name == "Eval replay, Luma" and " of " in rows[0].value
