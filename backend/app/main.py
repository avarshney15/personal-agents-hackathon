"""FastAPI app: every route in backend/API.md owned by Part 5. Owned by Part 5 (backend).

Run: cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
Current user, in order: cookie `ea_user`, header `X-User-Id`, else the demo user.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import registry, worker
from app.contracts import AskBefore, Event, FactGroups, FeedResponse, RsvpRun, User, VerifySession
from app.demo import demo_user, seed_demo_memory
from app.deps import USER_COOKIE, current_user
from app.store import get_store

log = logging.getLogger("api")

NOT_VERIFIED = ("Verify once with Persona before the agent can RSVP for you. "
                "Every RSVP tells the host a verified person is behind it.")


# ---------------------------------------------------------------- helpers


def _track(user_id: str, event: str, props: Optional[dict] = None) -> None:
    try:
        registry.get_telemetry().track(user_id, event, props or {})
    except Exception:
        log.exception("telemetry track %s failed", event)


# ---------------------------------------------------------------- startup


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store = get_store()
    recovered = worker.recover_on_startup()
    if recovered:
        log.info("marked %d unfinished runs as failed", recovered)
    store.ensure_user(demo_user())
    try:
        seed_demo_memory(registry.get_memory())
    except Exception:
        log.exception("seeding demo memory failed")
    def _warm_ranking() -> None:  # SIE encodes every event once; keep startup fast
        try:
            registry.get_ranking().encode_catalog(registry.get_catalog().events())
        except Exception:
            log.exception("encoding the catalog failed")

    threading.Thread(target=_warm_ranking, name="warm-ranking", daemon=True).start()
    yield
    try:
        registry.get_telemetry().flush()
    except Exception:
        log.exception("telemetry flush failed")


app = FastAPI(title="Event Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _feed():
    try:
        import app.feed as feed
    except ImportError as exc:
        raise HTTPException(503, f"The feed isn't ready yet ({exc}).") from exc
    return feed


# ---------------------------------------------------------------- health and me


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/me", response_model=User)
def me(user: User = Depends(current_user)) -> User:
    return user


class MePatch(BaseModel):
    spend_limit_usd: Optional[float] = None
    ask_before: Optional[AskBefore] = None
    usual_times: Optional[list[str]] = None
    name: Optional[str] = None


@app.patch("/api/me", response_model=User)
def patch_me(body: MePatch, user: User = Depends(current_user)) -> User:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "spend_limit_usd" in changes and changes["spend_limit_usd"] < 0:
        raise HTTPException(422, "spend_limit_usd can't be negative.")
    if "name" in changes and not changes["name"].strip():
        raise HTTPException(422, "name can't be empty.")
    return get_store().save_user(user.model_copy(update=changes))


@app.delete("/api/me")
def delete_me(response: Response, user: User = Depends(current_user)) -> dict:
    try:
        registry.get_memory().forget_all(user.id)
    except Exception:
        log.exception("forget_all failed for %s", user.id)
        raise HTTPException(502, "Couldn't clear memory; nothing was deleted.")
    get_store().delete_user(user.id)
    _track(user.id, "memory_forgotten", {"all": True, "account_deleted": True})
    response.delete_cookie(USER_COOKIE)
    return {"ok": True}


# ---------------------------------------------------------------- verify (Persona)


@app.post("/api/verify/start", response_model=VerifySession)
def verify_start(user: User = Depends(current_user)) -> VerifySession:
    session = registry.get_identity().start(user)  # url is None when the user already passed
    get_store().set_verified(user.id, user.verified, session.inquiry_id)
    _track(user.id, "verify_started", {"inquiry_id": session.inquiry_id})
    return session


def _verified_at(identity, user: User):
    try:
        return getattr(identity, "verified_at", lambda _u: None)(user)
    except Exception:
        log.exception("identity.verified_at failed")
        return None


def _mark_verified(identity, user: User, via: str) -> None:
    get_store().set_verified(user.id, True, verified_at=_verified_at(identity, user))
    _track(user.id, "verify_passed", {"inquiry_id": user.persona_inquiry_id, "via": via})


@app.get("/api/verify/status")
def verify_status(user: User = Depends(current_user)) -> dict:
    if user.verified:
        return {"verified": True}
    identity = registry.get_identity()
    verified = bool(identity.status(user))  # checks Persona directly, so polling is enough
    if verified:
        _mark_verified(identity, user, "status")
    return {"verified": verified}


@app.post("/api/verify/webhook")
async def verify_webhook(request: Request) -> dict:
    raw = await request.body()
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    identity = registry.get_identity()
    result = identity.on_webhook(payload if isinstance(payload, dict) else {}, raw_body=raw,
                                 signature=request.headers.get("Persona-Signature"))
    if result:
        user_id, verified = result
        user = get_store().get_user(user_id)
        if user and verified and not user.verified:  # verify once: a webhook never un-verifies
            _mark_verified(identity, user, "webhook")
    return {"ok": True}


# ---------------------------------------------------------------- For you


class IntoBody(BaseModel):
    text: str


@app.get("/api/feed", response_model=FeedResponse)
def get_feed(user: User = Depends(current_user)) -> FeedResponse:
    return _feed().build_feed(user, limit=20)


def _merge_chips(have: list[str], new: list[str]) -> list[str]:
    out, seen = list(have), {c.lower() for c in have}
    for chip in new:
        if chip and chip.lower() not in seen:
            out.append(chip)
            seen.add(chip.lower())
    return out


@app.post("/api/into", response_model=FeedResponse)
def add_into(body: IntoBody, user: User = Depends(current_user)) -> FeedResponse:
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Tell the agent what you're into.")
    feed = _feed()
    chips = feed.add_into(user, text)
    user = get_store().set_into(user.id, _merge_chips(user.into, chips)) or user
    return feed.build_feed(user, limit=20)  # feed.add_into tracks into_added


@app.delete("/api/into/{tag}", response_model=FeedResponse)
def remove_into(tag: str, user: User = Depends(current_user)) -> FeedResponse:
    feed = _feed()
    keep = [c for c in user.into if c.lower() != tag.strip().lower()]
    user = get_store().set_into(user.id, keep) or user
    return feed.build_feed(user, limit=20)


# ---------------------------------------------------------------- events and RSVP


@app.get("/api/events/{event_id}", response_model=Event)
def get_event(event_id: str, user: User = Depends(current_user)) -> Event:
    event = registry.get_catalog().get(event_id)
    if not event:
        raise HTTPException(404, f"No event {event_id}.")
    return event  # pick_opened is tracked by the Event sheet's /detail route


class RsvpBody(BaseModel):
    event_id: str


@app.post("/api/rsvp", response_model=RsvpRun)
def start_rsvp(body: RsvpBody, user: User = Depends(current_user)) -> RsvpRun:
    if not user.verified:
        raise HTTPException(403, NOT_VERIFIED)
    event = registry.get_catalog().get(body.event_id)
    if not event:
        raise HTTPException(404, f"No event {body.event_id}.")
    existing = get_store().active_run(user.id, event.id)
    if existing:
        return existing
    return worker.start_rsvp(user, event)


def _run_or_404(run_id: str) -> RsvpRun:
    run = get_store().get_run(run_id)
    if not run:
        raise HTTPException(404, f"No RSVP run {run_id}.")
    return run


@app.get("/api/rsvp/{run_id}", response_model=RsvpRun)
def get_rsvp(run_id: str) -> RsvpRun:
    return _run_or_404(run_id)


@app.get("/api/rsvp/{run_id}/frame")
def get_frame(run_id: str) -> FileResponse:
    _run_or_404(run_id)
    path = get_store().frame_path(run_id)
    if not path:
        raise HTTPException(404, "No screen yet.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


class AnswerBody(BaseModel):
    value: str


@app.post("/api/rsvp/{run_id}/answer", response_model=RsvpRun)
def answer_rsvp(run_id: str, body: AnswerBody) -> RsvpRun:
    run = _run_or_404(run_id)
    if not worker.answer(run_id, body.value.strip()):
        raise HTTPException(409, f"This run isn't waiting for you (status {run.status}).")
    return _run_or_404(run_id)


@app.post("/api/rsvp/{run_id}/cancel", response_model=RsvpRun)
def cancel_rsvp(run_id: str) -> RsvpRun:
    _run_or_404(run_id)
    return worker.cancel(run_id) or _run_or_404(run_id)


# ---------------------------------------------------------------- memory


@app.get("/api/memory", response_model=FactGroups)
def get_memory(user: User = Depends(current_user)) -> FactGroups:
    return registry.get_memory().facts(user.id)


@app.delete("/api/memory/{fact_id}", response_model=FactGroups)
def forget_fact(fact_id: str, user: User = Depends(current_user)) -> FactGroups:
    memory = registry.get_memory()
    memory.forget(user.id, fact_id)
    _track(user.id, "memory_forgotten", {"fact_id": fact_id})
    return memory.facts(user.id)


@app.delete("/api/memory", response_model=FactGroups)
def forget_all(user: User = Depends(current_user)) -> FactGroups:
    memory = registry.get_memory()
    memory.forget_all(user.id)
    get_store().set_into(user.id, [])  # the "Into right now" chips are memory too
    _track(user.id, "memory_forgotten", {"all": True})
    return memory.facts(user.id)


# ---------------------------------------------------------------- Part 8's routes


def _mount_extra() -> None:
    """Part 8's routes. A missing router is fine; a broken one is logged, not fatal."""
    if importlib.util.find_spec("app.extra.router") is None:
        return
    try:
        from app.extra.router import router as extra_router
    except Exception:
        log.exception("app/extra/router.py failed to import; its routes are off")
        return
    app.include_router(extra_router)


_mount_extra()
