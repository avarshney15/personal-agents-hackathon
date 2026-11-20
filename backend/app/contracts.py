"""Shared shapes and interfaces for Event Agent.

Owned by the lead. Every part imports from here and never redefines these.
To change anything in this file, ask the lead first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field

FactKind = Literal["said", "went", "skipped", "hidden"]
StepKey = Literal["checked", "opened", "told_host", "register", "read_back"]
StepState = Literal["done", "current", "waiting", "failed"]
RunStatus = Literal["queued", "running", "done", "needs_you", "failed"]
Arm = Literal["tfidf", "sie"]
PickTag = Literal["Strong match", "Something new"]

DEMO_USER_ID = "demo"

# Wording from the canvas (design/screens/Verify.dc.html and Rsvp.dc.html).
DISCLOSURE_TEMPLATE = (
    "Registered by Event Agent for {name}, a verified person. "
    "Reply to reach them directly."
)

STEP_ORDER: list[StepKey] = ["checked", "opened", "told_host", "register", "read_back"]

STEP_TEXT: dict[StepKey, tuple[str, str]] = {
    "checked": ("Checked the event", "Free, spots left, no host questions."),
    "opened": ("Opened Luma on your cloud desktop", "Signed in as you, from when you connected."),
    "told_host": ("Told the host who's registering", "“Registered by Event Agent for a verified person.”"),
    "register": ("Register", "One tap on Luma's own button."),
    "read_back": ("Check it worked", "Read your status back from the page."),
}


# ---------------------------------------------------------------- shapes


class Event(BaseModel):
    id: str
    title: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    venue: str = ""
    neighborhood: str = ""
    url: str
    source: Literal["luma", "seed"] = "seed"
    host: str = ""
    description: str = ""
    free: bool = True
    spots_left: Optional[int] = None
    has_questions: bool = False
    tags: list[str] = Field(default_factory=list)


class User(BaseModel):
    id: str
    name: str
    verified: bool = False
    persona_inquiry_id: Optional[str] = None
    into: list[str] = Field(default_factory=list)  # "Into right now" chips on For you


class Fact(BaseModel):
    id: str
    kind: FactKind
    text: str  # one plain sentence, shown as written on the Memory screen
    from_text: str  # where it came from, e.g. "You told the agent, Nov 18"
    event_id: Optional[str] = None
    created_at: datetime


class FactGroups(BaseModel):
    """The Memory screen's three groups. Hidden facts fold into skipped."""

    said: list[Fact] = Field(default_factory=list)
    went: list[Fact] = Field(default_factory=list)
    skipped: list[Fact] = Field(default_factory=list)


class Intent(BaseModel):
    taste: str  # text the ranker scores against, e.g. "climbing; AI agents"
    tags: list[str]  # chips, e.g. ["Climbing", "AI agents"]


class Pick(BaseModel):
    event: Event
    score: float
    why: str  # one line, quoting the remembered fact it came from
    why_fact_id: Optional[str] = None
    tag: PickTag
    arm: Arm


class FeedResponse(BaseModel):
    picks: list[Pick]
    into: list[str]
    arm: Arm


class RsvpStep(BaseModel):
    run_id: str
    key: StepKey
    title: str
    detail: str
    state: StepState


class RsvpRun(BaseModel):
    id: str
    user_id: str
    event_id: str
    status: RunStatus
    stop_reason: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    steps: list[RsvpStep] = Field(default_factory=list)
    has_frame: bool = False


class RsvpResult(BaseModel):
    status: Literal["done", "needs_you", "failed"]
    stop_reason: Optional[str] = None


class VerifySession(BaseModel):
    inquiry_id: str
    session_token: Optional[str] = None
    url: Optional[str] = None  # hosted Persona flow, opened by the Verify screen


# ---------------------------------------------------------------- interfaces


@runtime_checkable
class Memory(Protocol):
    """Cognee in production (app/sponsors/memory/cognee_memory.py: CogneeMemory)."""

    def remember(self, user_id: str, kind: FactKind, text: str, from_text: str,
                 event_id: Optional[str] = None) -> str: ...
    def recall(self, user_id: str, event: Event, k: int = 3) -> list[Fact]: ...
    def facts(self, user_id: str) -> FactGroups: ...
    def forget(self, user_id: str, fact_id: str) -> None: ...
    def forget_all(self, user_id: str) -> None: ...
    def taste(self, user_id: str) -> str: ...


@runtime_checkable
class Ranking(Protocol):
    """Superlinked SIE for arm "sie"; TF-IDF for arm "tfidf"
    (app/sponsors/ranking/sie_ranking.py: SieRanking)."""

    def encode_catalog(self, events: list[Event]) -> None: ...
    def score(self, taste: str, candidates: list[Event], arm: Arm) -> list[tuple[str, float]]: ...


@runtime_checkable
class Identity(Protocol):
    """Persona sandbox (app/sponsors/identity/persona_identity.py: PersonaIdentity)."""

    def start(self, user: User) -> VerifySession: ...
    def status(self, user: User) -> bool: ...
    def on_webhook(self, payload: dict) -> Optional[tuple[str, bool]]: ...  # (user_id, verified)
    def disclosure(self, user: User) -> str: ...


@runtime_checkable
class Telemetry(Protocol):
    """Amplitude + Statsig (app/sponsors/telemetry/live.py: LiveTelemetry)."""

    def wrap(self, client): ...  # anthropic.Anthropic -> traced client of the same type
    def track(self, user_id: str, event: str, props: Optional[dict] = None) -> None: ...
    def arm(self, user_id: str, experiment: str = "ranking") -> Arm: ...
    def flush(self) -> None: ...


@runtime_checkable
class Catalog(Protocol):
    """Seeded SF events plus the Luma reader (app/catalog/seed.py: SeedCatalog)."""

    def events(self) -> list[Event]: ...
    def get(self, event_id: str) -> Optional[Event]: ...


StepCallback = Callable[[RsvpStep], None]
FrameCallback = Callable[[bytes], None]  # PNG bytes of the hosted Chrome screen


@runtime_checkable
class RsvpAgent(Protocol):
    """Hosted Chrome + Claude computer use (app/act/browser_agent.py: BrowserRsvpAgent).

    Runs in a worker thread. Reports each of the five steps through on_step
    (in STEP_ORDER) and each new screenshot through on_frame. Never touches the store.
    """

    def run(self, run_id: str, user: User, event: Event, disclosure: str,
            on_step: StepCallback, on_frame: FrameCallback) -> RsvpResult: ...
