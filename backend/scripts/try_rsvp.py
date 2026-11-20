"""Run the real RSVP agent once on an event URL and print each step as it happens.

    cd backend
    .venv/bin/python scripts/login_luma.py            # once: sign in to Luma
    .venv/bin/python scripts/try_rsvp.py https://lu.ma/<event> --headed

Needs ANTHROPIC_API_KEY (in backend/.env or the shell). Frames go to ./frames/try_rsvp/.
If Luma asks for an emailed code, you're prompted for it here.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND / ".env")

from app.act.browser_agent import BrowserRsvpAgent  # noqa: E402
from app.contracts import DISCLOSURE_TEMPLATE, Event, RunNeed, RsvpStep, User  # noqa: E402

MARK = {"current": "…", "done": "✓", "failed": "✗", "waiting": "·"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BrowserRsvpAgent once.")
    parser.add_argument("url", help="Luma event URL")
    parser.add_argument("--title", default="Luma event")
    parser.add_argument("--name", default=os.getenv("DEMO_USER_NAME", "Maya R."))
    parser.add_argument("--headed", action="store_true", help="show the Chrome window")
    parser.add_argument("--profile", default=None, help="default: BROWSER_PROFILE_DIR")
    parser.add_argument("--model", default=None, help="default: RSVP_MODEL or claude-haiku-5-5")
    parser.add_argument("--frames", default="frames/try_rsvp")
    args = parser.parse_args()

    if not os.getenv("ANTHROPIC_API_KEY"):
        print("note: ANTHROPIC_API_KEY is not set; steps 3-5 need it (or an `ant auth` profile).")

    frames_dir = Path(args.frames)
    frames_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    count = {"n": 0}

    def on_step(step: RsvpStep) -> None:
        print(f"[{time.monotonic() - started:6.1f}s] {MARK[step.state]} {step.key:<10} "
              f"{step.state:<8} {step.title}: {step.detail}", flush=True)

    def on_frame(png: bytes) -> None:
        count["n"] += 1
        (frames_dir / f"{count['n']:03d}.png").write_bytes(png)
        (frames_dir / "latest.png").write_bytes(png)

    def ask_user(need: RunNeed):
        print(f"\nNEEDS YOU: {need.title}\n  {need.detail}")
        answer = input(f"  {need.input_label or 'Answer'} (blank to hand back): ").strip()
        return answer or None

    user = User(id="demo", name=args.name, verified=True)
    event = Event(id="try", title=args.title, starts_at=datetime.now(), url=args.url,
                  source="luma")
    agent = BrowserRsvpAgent(profile_dir=args.profile, headless=False if args.headed else None,
                             model=args.model)
    print(f"model {agent.model} · profile {Path(agent.profile_dir).resolve()} · "
          f"headless {agent.headless} · max {agent.max_actions} actions / "
          f"{int(agent.max_seconds)} s")
    result = agent.run("try", user, event, DISCLOSURE_TEMPLATE.format(name=user.name),
                       on_step, on_frame, ask_user)
    print(f"\nresult: {result.model_dump_json(indent=2)}")
    print(f"frames: {count['n']} in {frames_dir.resolve()}")
    return 0 if result.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
