"""Runs RSVPs in the background so they keep going after the browser tab closes. Owned by Part 5.

`start_rsvp` stores the run (five steps `waiting`, status `queued`) and returns at once; a worker
thread calls RsvpAgent.run(...) and saves each step and frame as they arrive.

Needs you: when the agent calls ask_user(need), the run shows `needs_you` with that need and the
agent's thread waits until `answer()` (POST /api/rsvp/{id}/answer), `cancel()`, or need.expires_at.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from app import registry
from app.contracts import Event, RsvpResult, RsvpRun, RsvpStep, RunNeed, User
from app.store import LOCAL_TZ, get_store

log = logging.getLogger("worker")

HOLD_MINUTES = 10
CANCELLED_REASON = "You cancelled this RSVP."

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rsvp")
_futures: dict[str, Future] = {}
_lock = threading.Lock()
_holds: dict[str, "_Hold"] = {}
_cancelled: set[str] = set()


class _Hold:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Optional[str] = None


def _track(user_id: str, event: str, props: Optional[dict] = None) -> None:
    try:
        registry.get_telemetry().track(user_id, event, props or {})
    except Exception:  # telemetry must never break a run
        log.exception("telemetry track %s failed", event)


def _arm(user_id: str):
    try:
        return registry.get_telemetry().arm(user_id)
    except Exception:
        return None


# ---------------------------------------------------------------- public


def start_rsvp(user: User, event: Event) -> RsvpRun:
    store = get_store()
    run = store.create_run(user.id, event.id, site=event.site, arm=_arm(user.id))
    _track(user.id, "rsvp_started", {"run_id": run.id, "event_id": event.id, "event_title": event.title,
                                     "site": event.site, "arm": run.arm})
    future = _executor.submit(_job, run.id, user, event)
    _futures[run.id] = future
    future.add_done_callback(lambda _f, rid=run.id: _futures.pop(rid, None))
    return run


def answer(run_id: str, value: Optional[str]) -> bool:
    """Hands the person's answer to the waiting agent. False if the run isn't waiting."""
    with _lock:
        hold = _holds.get(run_id)
    if not hold or hold.done.is_set():
        return False
    get_store().set_need(run_id, None)  # back to running before the agent wakes
    hold.value = value
    hold.done.set()
    return True


def cancel(run_id: str) -> Optional[RsvpRun]:
    """Stops a run that hasn't finished (or one handed back to the person)."""
    store = get_store()
    run = store.get_run(run_id)
    if not run or run.status in ("done", "failed"):
        return run
    with _lock:
        _cancelled.add(run_id)
        hold = _holds.get(run_id)
    if hold:
        hold.done.set()
    store.mark_steps(run_id, "current", "failed")
    store.finish_run(run_id, RsvpResult(status="failed", stop_reason=CANCELLED_REASON,
                                        cost_usd=run.cost_usd, model=run.model))
    _track(run.user_id, "rsvp_needs_you", {"run_id": run_id, "event_id": run.event_id, "status": "cancelled"})
    return store.get_run(run_id)


def is_waiting(run_id: str) -> bool:
    with _lock:
        hold = _holds.get(run_id)
    return bool(hold and not hold.done.is_set())


def wait(run_id: str, timeout: Optional[float] = None) -> None:
    """Block until a run's job finishes (tests and scripts)."""
    future = _futures.get(run_id)
    if future:
        future.result(timeout=timeout)


# ---------------------------------------------------------------- the job


def _is_cancelled(run_id: str) -> bool:
    with _lock:
        return run_id in _cancelled


def _make_ask_user(run_id: str, user: User, props: dict):
    store = get_store()

    def ask_user(need: RunNeed) -> Optional[str]:
        if _is_cancelled(run_id):
            return None
        now = datetime.now(timezone.utc)
        if need.expires_at is None:
            need = need.model_copy(update={"expires_at": now + timedelta(minutes=HOLD_MINUTES)})
        expires = need.expires_at if need.expires_at.tzinfo else need.expires_at.replace(tzinfo=timezone.utc)
        hold = _Hold()
        with _lock:
            _holds[run_id] = hold
        store.set_need(run_id, need)
        _track(user.id, "rsvp_needs_you", {**props, "kind": need.kind, "waiting": True})
        answered = hold.done.wait(max(0.0, (expires - now).total_seconds()))
        with _lock:
            _holds.pop(run_id, None)
        if _is_cancelled(run_id):
            return None
        if not answered:  # hold expired: take the need down, the agent decides what's next
            store.set_need(run_id, None)
            return None
        return hold.value

    return ask_user


def _spend_limit_need(user: User, spent: float) -> RunNeed:
    return RunNeed(
        kind="spend_limit", step_key="checked",
        title="The agent hit today's spend limit",
        detail=(f"It has spent ${spent:.2f} of your ${user.spend_limit_usd:.2f} a day. "
                "The agent stops and asks before going over."))


def _after_done(user: User, event: Event) -> None:
    """You're going: add it to the calendar and remember it."""
    try:
        registry.get_calendar().add_event(user.id, event)
    except Exception:
        log.exception("calendar add_event failed for %s", event.id)
    try:
        day = datetime.now(LOCAL_TZ).strftime("%b %-d")
        registry.get_memory().remember(user.id, "went", f"RSVP'd to {event.title}",
                                       f"The agent registered you, {day}", event_id=event.id)
    except Exception:
        log.exception("memory remember failed for %s", event.id)


def _telemetry_run(user: User, run_id: str, event: Event):
    """Telemetry.run(...) if the telemetry has it; a no-op context otherwise."""
    try:
        run = get_store().get_run(run_id)
        return registry.get_telemetry().run(user.id, run_id, arm=run.arm if run else None,
                                            event_id=event.id)
    except Exception:  # telemetry must never break a run
        log.exception("telemetry run context for %s failed", run_id)
        return contextlib.nullcontext()


def _job(run_id: str, user: User, event: Event) -> None:
    store = get_store()
    props = {"run_id": run_id, "event_id": event.id}
    if _is_cancelled(run_id):
        return
    try:
        spent = store.spent_today(user.id)
        if spent >= user.spend_limit_usd:
            result = RsvpResult(status="needs_you", stop_reason="Daily spend limit reached",
                                need=_spend_limit_need(user, spent))
            _track(user.id, "spend_limit_hit", {**props, "spent_usd": spent, "limit_usd": user.spend_limit_usd})
        else:
            store.set_status(run_id, "running")

            def on_step(step: RsvpStep) -> None:
                if _is_cancelled(run_id):
                    return
                if step.run_id != run_id:
                    step = step.model_copy(update={"run_id": run_id})
                store.save_step(step)
                _track(user.id, "rsvp_step", {**props, "step": step.key, "state": step.state})

            def on_frame(png: bytes) -> None:
                try:
                    store.save_frame(run_id, png)
                except Exception:
                    log.exception("saving frame for %s failed", run_id)

            agent = registry.get_rsvp_agent()
            disclosure = registry.get_identity().disclosure(user)
            kwargs = {}
            try:
                if "ask_user" in inspect.signature(agent.run).parameters:
                    kwargs["ask_user"] = _make_ask_user(run_id, user, props)
            except (TypeError, ValueError):
                pass
            with _telemetry_run(user, run_id, event):  # one Amplitude session per RSVP run
                result = agent.run(run_id, user, event, disclosure, on_step, on_frame, **kwargs)
    except Exception as exc:  # any crash ends the run as failed, with the reason shown
        log.exception("rsvp run %s failed", run_id)
        result = RsvpResult(status="failed",
                            stop_reason=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)

    if _is_cancelled(run_id):  # cancel() already wrote the final state
        return
    if result.status == "failed":
        store.mark_steps(run_id, "current", "failed")
    if result.status == "done" and result.outcome is None:
        result = result.model_copy(update={"outcome": "going"})
    store.finish_run(run_id, result)
    if result.status == "done":
        _after_done(user, event)
        _track(user.id, "rsvp_done", {**props, "outcome": result.outcome, "cost_usd": result.cost_usd})
    else:  # needs_you, or failed: either way the person has to step in
        _track(user.id, "rsvp_needs_you", {**props, "status": result.status, "stop_reason": result.stop_reason,
                                           "kind": result.need.kind if result.need else None})


def recover_on_startup() -> int:
    """Runs left queued/running/waiting by a previous process can't resume."""
    return get_store().fail_unfinished("The server restarted during this run. Try again.")
