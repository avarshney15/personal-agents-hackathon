"""Simulated RSVP: walks the five steps with short pauses. Same interface as BrowserRsvpAgent.

FAKE_RSVP_SCENARIO picks the story:
  going (default)  all five steps, outcome going
  code             asks for the code Luma emailed at step 2; carries on if answered
  waitlist         the event is full, so it joins the waitlist (outcome waitlist)
  paid             stops at step 1 and hands back: the agent never pays
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.contracts import (STEP_ORDER, STEP_TEXT, AskUserCallback, Event, FrameCallback,
                           RsvpResult, RsvpStep, RunNeed, StepCallback, User)

FAKE_MODEL = "fake"
FAKE_COST = {"going": 0.06, "code": 0.06, "waitlist": 0.05, "paid": 0.01}


class FakeRsvpAgent:
    def run(self, run_id: str, user: User, event: Event, disclosure: str,
            on_step: StepCallback, on_frame: FrameCallback,
            ask_user: Optional[AskUserCallback] = None) -> RsvpResult:
        pause = float(os.getenv("FAKE_STEP_SECONDS", "1.5"))
        scenario = os.getenv("FAKE_RSVP_SCENARIO", "going").strip().lower() or "going"
        cost = FAKE_COST.get(scenario, 0.06)

        def step(key, state, detail=None):
            title, default = STEP_TEXT[key]
            on_step(RsvpStep(run_id=run_id, key=key, title=title, detail=detail or default,
                             state=state))

        def hand_back(at: int, reason: str, need: RunNeed) -> RsvpResult:
            step(STEP_ORDER[at], "failed", reason)
            for key in STEP_ORDER[at + 1:]:
                step(key, "waiting")
            return RsvpResult(status="needs_you", stop_reason=reason, need=need,
                              cost_usd=cost, model=FAKE_MODEL)

        for i, key in enumerate(STEP_ORDER):
            detail = None
            if key == "told_host":
                detail = f"“{disclosure}”"
            if key == "checked" and scenario == "waitlist":
                detail = "Full, but Luma has a waitlist. No cost, no host questions."
            if key == "register" and scenario == "waitlist":
                detail = "One tap on Luma's own Join Waitlist button."
            if key == "read_back" and scenario == "waitlist":
                detail = "Luma shows “You're on the waitlist”."
            step(key, "current", detail)
            time.sleep(pause)

            if key == "checked" and scenario == "paid":
                reason = "It's a paid event. The agent never pays, so get the ticket yourself on Luma."
                return hand_back(i, reason, RunNeed(
                    kind="payment", title="This one costs money",
                    detail="The agent never pays. Get the ticket yourself on Luma if you want to go.",
                    step_key=key))

            if key == "opened" and scenario == "code":
                need = RunNeed(
                    kind="code", title="Enter the code Luma emailed you",
                    detail=("Your Luma login expired, so Luma checked it's you. The agent types "
                            "the code in; it never sees your inbox."),
                    step_key=key, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                    input_label="Six-digit code")
                answer = ask_user(need) if ask_user else None
                if not (answer or "").strip():
                    reason = "Luma needs the code it emailed you. Sign in to Luma again to finish."
                    return hand_back(i, reason, RunNeed(
                        kind="login", title="Sign in to Luma again", detail=reason,
                        step_key=key))
                time.sleep(pause)

            step(key, "done", detail)
        outcome = "waitlist" if scenario == "waitlist" else "going"
        return RsvpResult(status="done", outcome=outcome, cost_usd=cost, model=FAKE_MODEL)
