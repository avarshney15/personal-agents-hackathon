"""What the agent did (Activity) and the team dashboard (Ops), both from stored RSVP runs."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from app.contracts import (ActivityItem, ActivityResponse, ArmStat, OpsRun, OpsSummary, Rehearsal, RsvpRun,
                           SpendStatus, User)
from app.extra import data
from app.extra.store_extra import get_extra_store
from app.extra.week import ensure_demo_seed

log = logging.getLogger("extra.reports")

REPO_ROOT = Path(__file__).resolve().parents[3]
MIN_PICKS_PER_ARM = 20  # below this, the arm comparison shows the canvas's sample numbers

ARM_TEXT = {
    "sie": ("New: memory and reranking", "Recall from memory, then rerank the top 40"),
    "tfidf": ("Old: word matching", "Taste profile and word overlap"),
}

# The canvas's numbers (design/screens/Ops.dc.html), shown with sample=true when ours are too thin.
SAMPLE_ARMS = [
    ArmStat(arm="sie", label=ARM_TEXT["sie"][0], rsvp_rate=0.18, how=ARM_TEXT["sie"][1]),
    ArmStat(arm="tfidf", label=ARM_TEXT["tfidf"][0], rsvp_rate=0.11, how=ARM_TEXT["tfidf"][1]),
]
SAMPLE_REHEARSALS = [
    Rehearsal(name="Simulated Luma", value="48 of 50", ok=True),
    Rehearsal(name="Simulated Meetup", value="45 of 50", ok=True),
    Rehearsal(name="Full event, no waitlist", value="Stopped, as it should", ok=True),
    Rehearsal(name="Paid event", value="Asked first, as it should", ok=True),
]
SAMPLE_TILES = dict(runs_today=128, confirmed_pct=94.0, handed_to_person=6, cost_per_rsvp_usd=0.07)
SAMPLE_RECENT = [
    OpsRun(run="RSVP · Agent builders night", site="Luma", steps=5, model="Claude Haiku", cost_usd=0.06,
           outcome="Going", ok=True),
    OpsRun(run="RSVP · Design systems dinner", site="Partiful", steps=2, model="Claude Haiku", cost_usd=0.03,
           outcome="Needs code", ok=False),
    OpsRun(run="Waitlist · Bouldering social", site="Meetup", steps=4, model="Claude Haiku", cost_usd=0.05,
           outcome="Waitlist", ok=True),
    OpsRun(run="Tag 30 new events", site="Catalog", steps=1, model="Arcee", cost_usd=0.01, outcome="Done", ok=True),
]

NEED_OUTCOME = {
    "code": "Needs code", "payment": "Needs payment", "choice": "Needs a choice", "login": "Needs login",
    "human_check": "Needs a human check", "spend_limit": "Hit spend limit",
}
CANCELLED = "You cancelled this RSVP."


# ---------------------------------------------------------------- one run, in words


def _title(run: RsvpRun) -> str:
    event = data.get_event(run.event_id)
    return event.title if event else run.event_id


def _what(run: RsvpRun, title: str) -> str:
    return f"Joined waitlist · {title}" if run.outcome == "waitlist" else f"RSVP · {title}"


def _steps_done(run: RsvpRun) -> int:
    return sum(1 for s in run.steps if s.state == "done")


def _site(run: RsvpRun) -> str:
    return data.SITE_NAMES.get(run.site, run.site.title())


def _result(run: RsvpRun) -> tuple[str, str]:
    """(result, tone) for the Activity row."""
    if run.status == "done":
        return ("Waitlist", "wait") if run.outcome == "waitlist" else ("Going", "ok")
    if run.status == "needs_you":
        return "Needs you", "needs"
    if run.status == "failed":
        return ("Cancelled", "wait") if run.stop_reason == CANCELLED else ("Failed", "needs")
    return "In progress", "wait"


def _model(model: Optional[str]) -> str:
    if not model or model.lower() in ("fake", "simulated", "sim"):
        return "Simulated"
    low = model.lower()
    for name in ("haiku", "sonnet", "opus"):
        if name in low:
            return f"Claude {name.title()}"
    return model


# ---------------------------------------------------------------- Activity


def build_activity(user: User) -> ActivityResponse:
    ensure_demo_seed(user)
    runs = data.list_runs(user.id)[:50]
    asks = {(a["run_id"], a["kind"]): a for a in get_extra_store().asks(user.id)}
    items: list[ActivityItem] = []
    for run in runs:
        title = _title(run)
        result, tone = _result(run)
        at = run.ended_at or run.started_at
        steps = _steps_done(run)
        where = f"{_site(run)} · {data.clock_ampm(at)}" + (f" · {steps} step{'s' if steps != 1 else ''}" if steps else "")
        items.append(ActivityItem(id=run.id, what=_what(run, title), where=where, at=at, result=result, tone=tone,
                                  run_id=run.id))
        ask = asks.get((run.id, "code"))
        if ask and run.status in ("done", "running"):  # it asked, the person answered, the run carried on
            items.append(ActivityItem(id=f"{run.id}-code", what="Asked you for a code",
                                      where=f"{_site(run)} · {data.clock_ampm(ask['asked_at'])}", at=ask["asked_at"],
                                      result="Answered", tone="needs", run_id=run.id))

    connected = [s for s, on in get_extra_store().sites(user.id).items() if on]
    events = data.catalog_events()
    if connected:
        events = [e for e in events if e.site in connected]
    names = ", ".join(data.SITE_NAMES[s] for s in data.SITES if s in connected) or "Events in SF"
    read_at = data.catalog_loaded_at()
    items.append(ActivityItem(id="read-events", what="Read your events", where=f"{names} · {data.clock_ampm(read_at)}",
                              at=read_at, result=f"{len(events)} event{'s' if len(events) != 1 else ''}", tone="ok"))

    items.sort(key=lambda i: data.aware(i.at), reverse=True)
    spend = SpendStatus(spent_usd=round(data.spent_today(user), 4), limit_usd=user.spend_limit_usd)
    return ActivityResponse(items=items, spend=spend)


# ---------------------------------------------------------------- Ops


def _ops_outcome(run: RsvpRun) -> tuple[str, bool]:
    if run.status == "done":
        return ("Waitlist", True) if run.outcome == "waitlist" else ("Going", True)
    if run.status == "needs_you":
        return NEED_OUTCOME.get(run.need.kind, "Needs you") if run.need else "Needs you", False
    if run.status == "failed":
        return ("Cancelled", False) if run.stop_reason == CANCELLED else ("Failed", False)
    return "Running", True


def _arms(runs: list[RsvpRun]) -> Optional[list[ArmStat]]:
    shown = get_extra_store().picks_shown()
    if any(shown.get(arm, 0) < MIN_PICKS_PER_ARM for arm in ARM_TEXT):
        return None
    out = []
    for arm, (label, how) in ARM_TEXT.items():
        rsvps = sum(1 for r in runs if r.arm == arm)
        out.append(ArmStat(arm=arm, label=label, rsvp_rate=round(min(1.0, rsvps / shown[arm]), 4), how=how))
    return out


def rehearsals_path() -> Path:
    return Path(os.getenv("REHEARSALS_PATH") or REPO_ROOT / "evals" / "rehearsals.json")


def _rehearsals() -> Optional[list[Rehearsal]]:
    """Part 4's evals/rehearsals.json: a list (or {"rehearsals": [...]}) of {name, value, ok}
    or {name, passed, total}."""
    path = rehearsals_path()
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        rows = raw.get("rehearsals", raw.get("results", [])) if isinstance(raw, dict) else raw
        out = []
        for row in rows:
            if "value" not in row and "passed" in row and "total" in row:
                passed, total = int(row["passed"]), int(row["total"])
                row = {**row, "value": f"{passed} of {total}", "ok": row.get("ok", total and passed / total >= 0.9)}
            out.append(Rehearsal(name=str(row["name"]), value=str(row["value"]), ok=bool(row.get("ok", True))))
        return out or None
    except Exception:
        log.exception("couldn't read %s", path)
        return None


def build_ops(sample: bool = False) -> OpsSummary:
    if sample:
        return OpsSummary(**SAMPLE_TILES, arms=SAMPLE_ARMS, rehearsals=SAMPLE_REHEARSALS, recent=SAMPLE_RECENT,
                          sample=True)
    all_runs = data.list_runs()
    today = [r for r in all_runs if data.aware(r.started_at) >= data.today_start()]
    finished = [r for r in today if r.status in ("done", "needs_you", "failed")]
    done = [r for r in today if r.status == "done"]
    confirmed = [r for r in done if any(s.key == "read_back" and s.state == "done" for s in r.steps)]
    asked = {a["run_id"] for a in get_extra_store().asks() if any(r.id == a["run_id"] for r in today)}
    handed = {r.id for r in today if r.status == "needs_you"} | asked
    cost = sum(r.cost_usd for r in today)

    recent = []
    for run in all_runs[:10]:
        title = _title(run)
        outcome, ok = _ops_outcome(run)
        recent.append(OpsRun(run=("Waitlist · " if run.outcome == "waitlist" else "RSVP · ") + title, site=_site(run),
                             steps=_steps_done(run), model=_model(run.model), cost_usd=round(run.cost_usd, 4),
                             outcome=outcome, ok=ok))

    arms = _arms(all_runs)
    rehearsals = _rehearsals()
    return OpsSummary(
        runs_today=len(today),
        confirmed_pct=round(100.0 * len(confirmed) / len(finished), 1) if finished else 0.0,
        handed_to_person=len(handed),
        cost_per_rsvp_usd=round(cost / len(done), 4) if done else 0.0,
        arms=arms or SAMPLE_ARMS,
        rehearsals=rehearsals or SAMPLE_REHEARSALS,
        recent=recent,
        sample=arms is None or rehearsals is None,
    )
