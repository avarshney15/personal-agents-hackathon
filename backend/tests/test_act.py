"""Part 6: the RSVP agent. Real headless Chromium on a local Luma-like page, scripted Claude."""

from __future__ import annotations

import itertools
import threading
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlparse

import pytest

from app import contracts as c
from app.act import page_checks as pc
from app.act.browser_agent import BrowserRsvpAgent
from app.act.computer import to_playwright_key
from app.act.fake import FakeRsvpAgent

FIXTURES = Path(__file__).parent / "fixtures" / "act"
DISCLOSURE = c.DISCLOSURE_TEMPLATE.format(name="Maya R.")
USER = c.User(id="demo", name="Maya R.", verified=True)
NOTE_XY = [900, 438]  # centre of the fixture's note textarea
REGISTER_XY = [900, 544]  # centre of the fixture's register button

_ids = itertools.count(1)


# ------------------------------------------------------------------ scripted Claude


def computer(name, **inp):
    return NS(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp,
              toolset_name="computer")


def tool(name, **inp):
    return NS(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=inp, toolset_name=None)


def report(key, state, detail=""):
    return tool("report_step", key=key, state=state, detail=detail)


def finish(outcome, reason="", kind="none"):
    return tool("finish", outcome=outcome, kind=kind, reason=reason)


class FakeMessages:
    def __init__(self, turns, error=None):
        self.turns = list(turns)
        self.calls = []
        self.error = error

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        content = self.turns.pop(0) if self.turns else [NS(type="text", text="I'm stuck.")]
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        usage = NS(input_tokens=2000, output_tokens=300, cache_read_input_tokens=1000,
                   cache_creation_input_tokens=500)
        return NS(content=content, stop_reason=stop, usage=usage)


class FakeClient:
    def __init__(self, turns=(), error=None):
        self.messages = FakeMessages(turns, error)


# ------------------------------------------------------------------ local site


@pytest.fixture
def site():
    registered: list[dict] = []

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(FIXTURES), **kw)

        def do_GET(self):
            if self.path.startswith("/api/register"):
                q = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                registered.append({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
                return
            super().do_GET()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield NS(url=f"http://127.0.0.1:{server.server_port}/luma_like.html",
             registered=registered)
    server.shutdown()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("RSVP_MODEL", "RSVP_MAX_ACTIONS", "RSVP_MAX_SECONDS", "RSVP_FALLBACKS",
                 "RSVP_EFFORT", "BROWSER_HEADLESS"):
        monkeypatch.delenv(name, raising=False)


def event(site, variant, **kw):
    return c.Event(id=f"e-{variant}", title="Agent builders night",
                   starts_at=datetime(2026, 11, 24, 18, 30), url=f"{site.url}?variant={variant}",
                   source="luma", **kw)


def run(tmp_path, site, variant, turns=(), ask_user=None, error=None, **agent_kw):
    client = FakeClient(turns, error)
    agent = BrowserRsvpAgent(client=client, profile_dir=str(tmp_path / "profile"),
                             headless=True, **agent_kw)
    steps: list[c.RsvpStep] = []
    frames: list[bytes] = []
    result = agent.run("run-1", USER, event(site, variant), DISCLOSURE, steps.append,
                       frames.append, ask_user)
    return NS(result=result, steps=steps, frames=frames, client=client)


def final(steps):
    last = {}
    for s in steps:
        last[s.key] = s
    return {k: (last[k].state, last[k].detail) for k in last}


def assert_in_order(steps):
    idx = [c.STEP_ORDER.index(s.key) for s in steps]
    assert idx == sorted(idx), [s.key for s in steps]
    assert set(s.key for s in steps) == set(c.STEP_ORDER)


# ------------------------------------------------------------------ the happy path


def test_registers_and_writes_disclosure(tmp_path, site):
    out = run(tmp_path, site, "going", [
        [report("told_host", "current", "On the form."), computer("left_click", coordinate=NOTE_XY),
         computer("type", text=DISCLOSURE)],
        [report("told_host", "done", "Wrote the note."), report("register", "current"),
         computer("left_click", coordinate=REGISTER_XY)],
        [report("register", "done", "Clicked Register."),
         report("read_back", "done", "Luma shows You're going."), finish("going", "You're going.")],
    ])
    r = out.result
    assert (r.status, r.outcome, r.model) == ("done", "going", "claude-haiku-5-5")
    assert r.cost_usd > 0
    # The note field held the disclosure and Luma's own button was clicked.
    assert site.registered == [{"variant": "going", "note": DISCLOSURE}]
    assert_in_order(out.steps)
    states = final(out.steps)
    assert all(state == "done" for state, _ in states.values())
    assert states["told_host"][1] == f"“{DISCLOSURE}”"
    assert states["read_back"][1] == "Luma shows You're going."
    assert [s.state for s in out.steps if s.key == "told_host"] == ["current", "done"]
    # Frames: the first look plus a screenshot after each turn's actions.
    assert len(out.frames) >= 3 and all(f.startswith(b"\x89PNG") for f in out.frames)

    calls = out.client.messages.calls
    assert len(calls) == 3
    assert {"type": "computer_toolset_20260801"} in calls[0]["tools"]
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert "extra_body" not in calls[0]  # no server-side fallback on Haiku 5.5
    first = calls[0]["messages"][0]["content"]
    assert first[0]["type"] == "text" and first[1]["type"] == "image"  # text before screenshot
    results = [b for m in calls[-1]["messages"] if m["role"] == "user" and
               isinstance(m["content"], list) for b in m["content"] if b.get("type") == "tool_result"]
    computer_ids = {b.id for turn in calls[-1]["messages"] if turn["role"] == "assistant"
                    for b in turn["content"] if getattr(b, "toolset_name", None) == "computer"}
    echoed = {b["tool_use_id"] for b in results if b.get("toolset_name") == "computer"}
    assert computer_ids and computer_ids == echoed


def test_no_note_field_says_so_and_continues(tmp_path, site):
    out = run(tmp_path, site, "nonote", [
        [report("told_host", "done", "No note field on this form."), report("register", "current"),
         computer("left_click", coordinate=REGISTER_XY)],
        [finish("going", "You're going.")],
    ])
    assert (out.result.status, out.result.outcome) == ("done", "going")
    assert site.registered == [{"variant": "nonote", "note": ""}]
    states = final(out.steps)
    assert "no note field" in states["told_host"][1].lower()
    assert states["read_back"] == ("done", "Luma shows “You're going”.")
    assert_in_order(out.steps)


def test_opus_sends_server_side_fallbacks(tmp_path, site):
    out = run(tmp_path, site, "nonote", [[computer("left_click", coordinate=REGISTER_XY)],
                                         [finish("going")]], model="claude-opus-5-5")
    assert out.result.model == "claude-opus-5-5"
    call = out.client.messages.calls[0]
    assert call["extra_body"] == {"fallbacks": "default"}
    assert call["extra_headers"] == {"anthropic-beta": "server-side-fallback-2026-07-01"}


# ------------------------------------------------------------------ step 1 and 2 hand-backs


def assert_handed_back(out, step, kind):
    r = out.result
    assert r.status == "needs_you" and r.need is not None
    assert (r.need.kind, r.need.step_key) == (kind, step)
    states = final(out.steps)
    assert states[step][0] == "failed"
    later = c.STEP_ORDER[c.STEP_ORDER.index(step) + 1:]
    assert all(states[k][0] == "waiting" for k in later)
    assert_in_order(out.steps)


def test_paid_event_stops_at_step_one(tmp_path, site):
    out = run(tmp_path, site, "paid")
    assert_handed_back(out, "checked", "payment")
    assert "never pays" in out.result.stop_reason
    assert out.client.messages.calls == []  # decided from the page, no Claude call
    assert site.registered == []


def test_host_questions_stop_at_step_one(tmp_path, site):
    out = run(tmp_path, site, "questions")
    assert_handed_back(out, "checked", "choice")
    assert "questions" in out.result.stop_reason
    assert out.client.messages.calls == []


def test_full_event_without_waitlist_stops(tmp_path, site):
    out = run(tmp_path, site, "full")
    assert_handed_back(out, "checked", "choice")


def test_signed_out_profile_stops_at_step_two(tmp_path, site):
    out = run(tmp_path, site, "signin")
    assert_handed_back(out, "opened", "login")
    assert out.result.stop_reason == "Sign in to Luma in hosted Chrome (run scripts/login_luma.py)"
    assert final(out.steps)["checked"][0] == "done"


def test_login_code_asks_the_person_and_carries_on(tmp_path, site):
    asked: list[c.RunNeed] = []

    def ask_user(need):
        asked.append(need)
        return "123456"

    out = run(tmp_path, site, "code", [
        [computer("left_click", coordinate=NOTE_XY), computer("type", text=DISCLOSURE),
         computer("left_click", coordinate=REGISTER_XY)],
        [finish("going", "You're going.")],
    ], ask_user=ask_user)
    assert [(n.kind, n.step_key, n.input_label) for n in asked] == \
        [("code", "opened", "Six-digit code")]
    assert asked[0].expires_at is not None
    assert (out.result.status, out.result.outcome) == ("done", "going")
    assert site.registered == [{"variant": "code", "note": DISCLOSURE}]


def test_login_code_unanswered_hands_back(tmp_path, site):
    out = run(tmp_path, site, "code", ask_user=lambda need: None)
    assert_handed_back(out, "opened", "login")


def test_waitlist_is_joined(tmp_path, site):
    out = run(tmp_path, site, "waitlist", [
        [report("told_host", "done", "No note field on this form."),
         computer("left_click", coordinate=REGISTER_XY)],
        [report("read_back", "done", "You're on the waitlist."), finish("waitlist")],
    ])
    assert (out.result.status, out.result.outcome) == ("done", "waitlist")
    assert site.registered == [{"variant": "waitlist", "note": ""}]
    assert "waitlist" in final(out.steps)["checked"][1]
    assert "Join its waitlist" in out.client.messages.calls[0]["messages"][0]["content"][0]["text"]
    assert final(out.steps)["register"][1] == "One tap on Luma's own Join Waitlist button."


# ------------------------------------------------------------------ the real SDK on the wire


def test_real_sdk_request_shape(tmp_path, site):
    """Drives the loop through anthropic.Anthropic against a local /v1/messages stand-in."""
    import json as _json

    import anthropic

    def msg(*content, stop="tool_use"):
        return {"id": f"msg_{next(_ids)}", "type": "message", "role": "assistant",
                "model": "claude-haiku-5-5", "content": list(content), "stop_reason": stop,
                "stop_sequence": None,
                "usage": {"input_tokens": 1200, "output_tokens": 150,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}

    def use(name, inp, toolset=True):
        block = {"type": "tool_use", "id": f"toolu_{next(_ids)}", "name": name, "input": inp}
        if toolset:
            block["toolset_name"] = "computer"
        return block

    thinking = {"type": "thinking", "thinking": "", "signature": "sig-abc"}
    replies = [
        msg(thinking, use("left_click", {"coordinate": NOTE_XY}),
            use("type", {"text": DISCLOSURE})),
        msg(thinking, use("left_click", {"coordinate": REGISTER_XY})),
        msg(use("finish", {"outcome": "going", "kind": "none", "reason": "You're going."},
                toolset=False)),
    ]
    bodies: list[dict] = []
    headers: list[dict] = []

    from http.server import BaseHTTPRequestHandler

    class Api(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            bodies.append(_json.loads(body))
            headers.append(dict(self.headers))
            out = _json.dumps(replies.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=api.serve_forever, daemon=True).start()
    try:
        client = anthropic.Anthropic(api_key="test", max_retries=0,
                                     base_url=f"http://127.0.0.1:{api.server_port}")
        agent = BrowserRsvpAgent(client=client, profile_dir=str(tmp_path / "profile"),
                                 headless=True)
        steps: list[c.RsvpStep] = []
        result = agent.run("run-sdk", USER, event(site, "going"), DISCLOSURE, steps.append,
                           lambda _: None)
    finally:
        api.shutdown()

    assert (result.status, result.outcome) == ("done", "going"), result.stop_reason
    assert site.registered == [{"variant": "going", "note": DISCLOSURE}]
    first, last = bodies[0], bodies[-1]
    assert first["model"] == "claude-haiku-5-5"
    assert {"type": "computer_toolset_20260801"} in first["tools"]
    assert first["output_config"] == {"effort": "medium"}
    assert first["cache_control"] == {"type": "ephemeral"}
    assert "fallbacks" not in first and "anthropic-beta" not in {k.lower() for k in headers[0]}
    # The assistant turn goes back unchanged: thinking signature and toolset_name intact.
    assistant = last["messages"][1]["content"]
    assert assistant[0] == thinking
    assert all(b["toolset_name"] == "computer" for b in assistant if b["type"] == "tool_use")
    tool_results = last["messages"][2]["content"]
    assert [b["toolset_name"] for b in tool_results] == ["computer", "computer"]
    assert tool_results[-1]["content"][-1]["type"] == "image"
    assert result.cost_usd == pytest.approx(3 * (1200 * 0.10 + 150 * 0.50) / 1e6, abs=1e-4)


# ------------------------------------------------------------------ limits and safety


def test_action_limit_hands_back(tmp_path, site):
    shots = [[computer("screenshot")] for _ in range(5)]
    out = run(tmp_path, site, "going", shots, max_actions=2)
    assert_handed_back(out, "told_host", "choice")
    assert "2 actions" in out.result.stop_reason


def test_card_numbers_are_never_typed(tmp_path, site):
    out = run(tmp_path, site, "going", [[computer("left_click", coordinate=NOTE_XY),
                                         computer("type", text="4242 4242 4242 4242")]])
    assert_handed_back(out, "told_host", "payment")


def test_model_hand_back_carries_its_kind(tmp_path, site):
    out = run(tmp_path, site, "going", [[finish("needs_you", "A captcha appeared.",
                                                kind="human_check")]])
    assert_handed_back(out, "told_host", "human_check")
    assert out.result.stop_reason == "A captcha appeared."


def test_errors_fail_the_run_with_the_reason(tmp_path, site):
    out = run(tmp_path, site, "going", error=RuntimeError("API down"))
    r = out.result
    assert r.status == "failed" and "RuntimeError: API down" in r.stop_reason
    assert final(out.steps)["told_host"][0] == "failed"
    assert r.model == "claude-haiku-5-5"


# ------------------------------------------------------------------ pure helpers


def test_key_names_map_to_playwright():
    assert to_playwright_key("Return") == "Enter"
    assert to_playwright_key("ctrl+shift+Tab") == "Control+Shift+Tab"
    assert to_playwright_key("super+a") == "Meta+a"
    assert to_playwright_key("Page_Down") == "PageDown"
    assert to_playwright_key("f5") == "F5"


def test_event_fields_alone_can_stop():
    scan = {"text": "", "buttons": ["Register"], "fields": []}
    base = dict(id="e", title="t", starts_at=datetime(2026, 11, 24), url="https://lu.ma/x")
    assert pc.check_event(scan, c.Event(**base, free=False)).reason == pc.STOP_PAID
    assert pc.check_event(scan, c.Event(**base, has_questions=True)).reason == pc.STOP_QUESTIONS
    assert pc.check_event(scan, c.Event(**base)).ok
    assert pc.check_event({**scan, "text": "You're going"}, c.Event(**base)).already_going


def test_descriptions_do_not_fool_the_checks():
    ev = c.Event(id="e", title="t", starts_at=datetime(2026, 11, 24), url="https://lu.ma/x")
    text = ("If you're in SF this week, come hack with us! Last year we sold out in a day.\n"
            "Pay what you can for pizza.")
    check = pc.check_event({"text": text, "buttons": ["Pay it forward", "Register"],
                            "fields": []}, ev)
    assert check.ok and not check.already_going and not check.waitlist and not check.inconclusive


def test_luma_page_data_decides(tmp_path):
    ev = c.Event(id="e", title="t", starts_at=datetime(2026, 11, 24), url="https://lu.ma/x")

    def scan(ticket_info, questions=(), buttons=("Register",)):
        data = {"props": {"pageProps": {"initialData": {"data": {
            "ticket_info": ticket_info, "registration_questions": list(questions)}}}}}
        import json
        return {"text": "", "buttons": list(buttons), "fields": [], "next": json.dumps(data)}

    free = {"is_free": True, "is_sold_out": False, "spots_remaining": None}
    assert pc.check_event(scan(free), ev).ok
    assert pc.check_event(scan({**free, "is_free": False}), ev).reason == pc.STOP_PAID
    assert pc.check_event(scan(free, [{"label": "LinkedIn?"}]), ev).reason == pc.STOP_QUESTIONS
    assert pc.check_event(scan({**free, "is_sold_out": True}, buttons=["Join Waitlist"]),
                          ev).waitlist
    assert pc.check_event(scan({**free, "spots_remaining": 0}), ev).reason == pc.STOP_FULL


# ------------------------------------------------------------------ the fake


@pytest.mark.parametrize("scenario,status,outcome", [
    ("going", "done", "going"), ("waitlist", "done", "waitlist"), ("paid", "needs_you", None)])
def test_fake_scenarios(monkeypatch, scenario, status, outcome):
    monkeypatch.setenv("FAKE_STEP_SECONDS", "0")
    monkeypatch.setenv("FAKE_RSVP_SCENARIO", scenario)
    steps = []
    ev = c.Event(id="e", title="t", starts_at=datetime(2026, 11, 24), url="https://lu.ma/x")
    r = FakeRsvpAgent().run("r", USER, ev, DISCLOSURE, steps.append, lambda _: None)
    assert (r.status, r.outcome, r.model) == (status, outcome, "fake")
    assert r.cost_usd > 0
    if scenario == "paid":
        assert r.need.kind == "payment"


def test_fake_code_scenario_asks(monkeypatch):
    monkeypatch.setenv("FAKE_STEP_SECONDS", "0")
    monkeypatch.setenv("FAKE_RSVP_SCENARIO", "code")
    ev = c.Event(id="e", title="t", starts_at=datetime(2026, 11, 24), url="https://lu.ma/x")
    asked = []
    r = FakeRsvpAgent().run("r", USER, ev, DISCLOSURE, lambda s: None, lambda _: None,
                            lambda need: asked.append(need) or "123456")
    assert r.status == "done" and asked[0].kind == "code" and asked[0].step_key == "opened"
    r = FakeRsvpAgent().run("r", USER, ev, DISCLOSURE, lambda s: None, lambda _: None,
                            lambda need: None)
    assert r.status == "needs_you" and r.need.kind == "login"
