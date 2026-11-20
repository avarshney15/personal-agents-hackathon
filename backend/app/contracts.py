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
PickTag = Literal["Strong match", "Match", "Something new"]
Site = Literal["luma", "partiful", "meetup", "eventbrite"]
WeekStatus = Literal["going", "waitlist", "invited", "pending"]
NeedKind = Literal["code", "payment", "choice", "login", "human_check", "spend_limit"]
Outcome = Literal["going", "waitlist"]
AskBefore = Literal["paid", "always", "never"]

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
    site: Site = "luma"
    going_count: Optional[int] = None  # "80 going"
    friends_going: list[str] = Field(default_factory=list)  # "Dev, Priya and 4 others you know"
    price_text: str = ""  # "Free", "$25"


class User(BaseModel):
    id: str
    name: str
    verified: bool = False
    persona_inquiry_id: Optional[str] = None
    into: list[str] = Field(default_factory=list)  # "Into right now" chips on For you
    email: Optional[str] = None
    usual_times: list[str] = Field(default_factory=list)  # "Weekday evenings", "Weekend days", "Mornings"
    spend_limit_usd: float = 2.0  # daily agent spend cap (canvas: "$2.00 a day")
    ask_before: AskBefore = "paid"  # "Ask before RSVPing: Only for paid events"
    verified_at: Optional[datetime] = None


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


class RunNeed(BaseModel):
    """What the agent is waiting on (canvas: NeedsYou.dc.html)."""

    kind: NeedKind
    title: str  # "Enter the code Luma emailed you"
    detail: str  # "Your Luma login expired, so Luma checked it's you. ..."
    step_key: StepKey
    expires_at: Optional[datetime] = None  # "we hold your place for 10 minutes"
    input_label: Optional[str] = None  # "Six-digit code"; None when there is nothing to type


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
    need: Optional[RunNeed] = None  # set while status == "needs_you"
    outcome: Optional[Outcome] = None
    cost_usd: float = 0.0
    model: Optional[str] = None
    site: Site = "luma"
    arm: Optional[Arm] = None  # ranking arm the user was in when they tapped RSVP


class RsvpResult(BaseModel):
    status: Literal["done", "needs_you", "failed"]
    stop_reason: Optional[str] = None
    outcome: Optional[Outcome] = None
    cost_usd: float = 0.0
    model: Optional[str] = None
    need: Optional[RunNeed] = None  # why it handed back, when status == "needs_you"


# ---------------------------------------------------------------- shapes for the new screens


class SiteConnection(BaseModel):
    site: Site
    name: str  # "Luma"
    connected: bool
    event_count: int = 0  # "Connected · 14 events"


class CalendarItem(BaseModel):
    id: str
    title: str  # "Call with Sam"
    starts_at: datetime
    ends_at: datetime


class Clash(BaseModel):
    event_id: str
    item: CalendarItem
    minutes_late: int  # "You'd arrive about 30 minutes late."
    text: str  # "Agent builders night starts at 6:30, and you have “Call with Sam” from 6:00 to 7:00."


class WeekItem(BaseModel):
    event: Event
    status: WeekStatus
    run_id: Optional[str] = None


class WeekDay(BaseModel):
    date: str  # ISO date
    label: str  # "Today", "Nov 24"
    weekday: str  # "Friday"
    items: list[WeekItem]


class WeekResponse(BaseModel):
    days: list[WeekDay]
    clashes: list[Clash]


class TasteRequest(BaseModel):
    text: str = ""  # "Climbing and AI agents lately. Not big networking mixers."
    chips: list[str] = Field(default_factory=list)  # "AI agents", "Climbing", ...
    usual_times: list[str] = Field(default_factory=list)


class AskResponse(BaseModel):
    query: str
    summary: str  # "Three fit. I left out two that are over 300 people, since you said not too big."
    filters: str  # "Next week · evenings and weekend · under 300"
    picks: list[Pick]


class EventDetail(BaseModel):
    event: Event
    tag: Optional[PickTag] = None
    why: str  # "Why it fits you", a few sentences
    remembered: list[Fact]  # "What the agent remembers"
    clash: Optional[Clash] = None
    week_status: Optional[WeekStatus] = None


class ActivityItem(BaseModel):
    id: str
    what: str  # "RSVP · Agent builders night"
    where: str  # "Luma · 2:14 pm · 5 steps"
    at: datetime
    result: str  # "Going", "Answered", "Waitlist", "17 events"
    tone: Literal["ok", "wait", "needs"]
    run_id: Optional[str] = None


class SpendStatus(BaseModel):
    spent_usd: float
    limit_usd: float


class ActivityResponse(BaseModel):
    items: list[ActivityItem]
    spend: SpendStatus


class ArmStat(BaseModel):
    arm: Arm
    label: str  # "New: memory and reranking"
    rsvp_rate: float  # share of For you picks people RSVP to, 0..1
    how: str


class Rehearsal(BaseModel):
    name: str  # "Simulated Luma"
    value: str  # "48 of 50"
    ok: bool


class OpsRun(BaseModel):
    run: str
    site: str
    steps: int
    model: str
    cost_usd: float
    outcome: str
    ok: bool


class OpsSummary(BaseModel):
    runs_today: int
    confirmed_pct: float  # 0..100
    handed_to_person: int
    cost_per_rsvp_usd: float
    arms: list[ArmStat]
    rehearsals: list[Rehearsal]
    recent: list[OpsRun]
    sample: bool  # True when any section shows sample data rather than ours


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


@runtime_checkable
class Calendar(Protocol):
    """The user's own calendar, read only for clashes (canvas: Week, Event, Going).
    Fake with demo meetings by default; Airbyte Google Calendar in stretch
    (app/extra/calendar_airbyte.py: AirbyteCalendar)."""

    def items(self, user_id: str, start: datetime, end: datetime) -> list[CalendarItem]: ...
    def add_event(self, user_id: str, event: Event) -> bool: ...  # "Added to your calendar"


StepCallback = Callable[[RsvpStep], None]
FrameCallback = Callable[[bytes], None]  # PNG bytes of the hosted Chrome screen
# Blocks until the user answers on the Needs you screen; returns their input
# (e.g. the six-digit code), or None if they cancel or the hold expires.
AskUserCallback = Callable[[RunNeed], Optional[str]]


@runtime_checkable
class RsvpAgent(Protocol):
    """Hosted Chrome + Claude computer use (app/act/browser_agent.py: BrowserRsvpAgent).

    Runs in a worker thread. Reports each of the five steps through on_step
    (in STEP_ORDER) and each new screenshot through on_frame. When the site needs
    something only the user has (a login code), it calls ask_user and carries on
    with the answer. Never touches the store.
    """

    def run(self, run_id: str, user: User, event: Event, disclosure: str,
            on_step: StepCallback, on_frame: FrameCallback,
            ask_user: Optional[AskUserCallback] = None) -> RsvpResult: ...
