"""Simulated RSVP: walks the five steps with short pauses. Same interface as BrowserRsvpAgent."""

from __future__ import annotations

import os
import time

from app.contracts import (STEP_ORDER, STEP_TEXT, Event, FrameCallback, RsvpResult, RsvpStep,
                           StepCallback, User)


class FakeRsvpAgent:
    def run(self, run_id: str, user: User, event: Event, disclosure: str,
            on_step: StepCallback, on_frame: FrameCallback) -> RsvpResult:
        pause = float(os.getenv("FAKE_STEP_SECONDS", "1.5"))
        for i, key in enumerate(STEP_ORDER):
            title, detail = STEP_TEXT[key]
            if key == "told_host":
                detail = f"“{disclosure}”"
            on_step(RsvpStep(run_id=run_id, key=key, title=title, detail=detail, state="current"))
            time.sleep(pause)
            on_step(RsvpStep(run_id=run_id, key=key, title=title, detail=detail, state="done"))
        return RsvpResult(status="done")
