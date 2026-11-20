"""For you, Ask, and the Event sheet's "Why it fits you". Owned by Part 2.

build_feed and add_into back For you (Part 5's /api/feed and /api/into routes). ask and event_why back
Ask and the Event sheet (Part 8's /api/ask and /api/events/{id}/detail).

Scoring: the user's arm (Statsig, "sie" or "tfidf") picks how the ranker scores. Taste is the "Into
right now" chips (weigh most) plus memory.taste(); each part's scores are scaled by its best one and
blended. Events in the user's usual times get a small boost; events whose closest memory is a skip get
a penalty. Each pick's why quotes the remembered fact it came from.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from app import registry
from app.catalog import text as text_jobs
from app.contracts import Arm, AskResponse, Event, Fact, FeedResponse, Memory, Pick, PickTag, User

log = logging.getLogger("feed")

INTO_WEIGHT = 0.7  # "Into right now" chips vs memory.taste()
TOPIC_WEIGHT = 0.75  # Ask: the query's topic vs the user's taste
TIME_BOOST = 0.1  # added when an event falls in one of the user's usual times
SKIP_PENALTY = 0.5  # multiplier when the event's closest memory is a skip
STRONG_MIN = 0.45  # blended taste score (0..1) needed for "Strong match", with a matching fact
TOPIC_MIN = 0.35  # Ask: topic score (0..1, vs the best) needed to count as fitting
MAX_CANDIDATES = 200  # SieRanking encodes these once and reranks the top 40 with SIE score()
RECALL_K = 3
ASK_LIMIT = 10
RECALL_WORKERS = max(1, int(os.getenv("FEED_RECALL_WORKERS", "4") or 4))

POSITIVE = ("said", "went")
NEGATIVE = ("skipped", "hidden")

_pool = ThreadPoolExecutor(max_workers=max(2, RECALL_WORKERS), thread_name_prefix="feed")


# ---------------------------------------------------------------- public functions


def build_feed(user: User, limit: int = 20) -> FeedResponse:
    memory, ranking, telemetry = registry.get_memory(), registry.get_ranking(), registry.get_telemetry()
    arm: Arm = telemetry.arm(user.id, "ranking")
    events = _candidates()
    _ensure_encoded(ranking, events)
    scores = _blend(ranking, _taste_parts(user, memory), events, arm)
    slots = _usual_slots(user)
    order = sorted(events, key=lambda e: -(scores.get(e.id, 0.0) + (TIME_BOOST if _in_slots(e, slots) else 0.0)))
    head = order[:limit]
    rows = _rows(head, scores, _recall_many(memory, user.id, head), slots)
    picks = _make_picks(rows, arm, weak="Something new")
    telemetry.track(user.id, "pick_shown", {
        "arm": arm, "count": len(picks), "strong": sum(p.tag == "Strong match" for p in picks),
        "event_ids": [p.event.id for p in picks], "into": list(user.into),
    })
    return FeedResponse(picks=picks, into=list(user.into), arm=arm)


def add_into(user: User, text: str) -> list[str]:
    """Saves what they're into as a "said" fact and returns the new chips (newest first)."""
    intent, avoid = text_jobs.read_taste(text)
    if not intent.tags and not avoid:
        return list(user.into)
    memory, telemetry = registry.get_memory(), registry.get_telemetry()
    from_text = f"You told the agent, {_today_label()}"
    if intent.tags:
        memory.remember(user.id, "said", f"Into {_join(intent.tags)} lately", from_text)
    for phrase in avoid:
        memory.remember(user.id, "skipped", f"Not into {phrase}", from_text)
    telemetry.track(user.id, "into_added", {"tags": intent.tags, "taste": intent.taste, "avoid": avoid})
    return _merge(intent.tags, user.into)


def ask(user: User, text: str) -> AskResponse:
    """'Agent hackathons next week, evenings or weekend, not too big' -> filtered, ranked picks."""
    query = text_jobs.read_ask(text)
    memory, ranking, telemetry = registry.get_memory(), registry.get_ranking(), registry.get_telemetry()
    arm: Arm = telemetry.arm(user.id, "ranking")
    events = _candidates()
    _ensure_encoded(ranking, events)

    in_window = [e for e in events if _fits_when(e, query)]
    if query.topic:
        topic = _score(ranking, query.topic, in_window, arm)
        fitting = [e for e in in_window if topic.get(e.id, 0.0) >= TOPIC_MIN]
    else:
        topic, fitting = {}, in_window
    left_out = [e for e in fitting
                if query.max_going and e.going_count is not None and e.going_count > query.max_going]
    kept = [e for e in fitting if e not in left_out]

    taste = _blend(ranking, _taste_parts(user, memory), kept, arm)
    if query.topic:
        scores = {e.id: TOPIC_WEIGHT * topic.get(e.id, 0.0) + (1 - TOPIC_WEIGHT) * taste.get(e.id, 0.0) for e in kept}
    else:
        scores = taste
    slots = _usual_slots(user)
    head = sorted(kept, key=lambda e: -scores.get(e.id, 0.0))[:ASK_LIMIT]
    rows = _rows(head, scores, _recall_many(memory, user.id, head), slots)
    picks = _make_picks(rows, arm, weak="Match", topic=query.topic)

    summary = _ask_summary(len(picks), len(left_out), query)
    telemetry.track(user.id, "ask_query", {
        "query": text, "filters": query.filters, "topic": query.topic, "arm": arm,
        "results": len(picks), "left_out": len(left_out), "event_ids": [p.event.id for p in picks],
    })
    return AskResponse(query=text, summary=summary, filters=query.filters, picks=picks)


def event_why(user: User, event: Event) -> tuple[str, list[Fact], Optional[PickTag]]:
    """The Event sheet's "Why it fits you" (2-3 sentences), the facts it quotes, and the tag."""
    memory, ranking, telemetry = registry.get_memory(), registry.get_ranking(), registry.get_telemetry()
    arm: Arm = telemetry.arm(user.id, "ranking")
    facts = _recall_many(memory, user.id, [event])[event.id]
    positive = [f for f in facts if f.kind in POSITIVE and _fits_time(f, event)]
    skip = facts[0] if facts and facts[0].kind in NEGATIVE else None

    events = _candidates()
    if all(e.id != event.id for e in events):
        events.append(event)
    _ensure_encoded(ranking, events)
    taste = _blend(ranking, _taste_parts(user, memory), events, arm).get(event.id, 0.0)
    in_time = _in_slots(event, _usual_slots(user))

    went = next((f for f in positive if f.kind == "went"), None)
    said = next((f for f in positive if f.kind == "said"), None)
    used = [f for f in (went, said) if f is not None]
    sentences: list[str] = []
    if skip is not None:
        sentences.append(f"Heads up: {skip.text.rstrip('.')} ({_lower_first(skip.from_text.rstrip('.'))}).")
        used.insert(0, skip)
    if went:
        sentences.append(_fact_sentence(went))
    sentences.append(_event_sentence(event, in_time))
    if said:
        sentences.append(_fact_sentence(said))
    if not positive and skip is None:
        sentences.insert(0, "Nothing you've told the agent points here yet, so this is something new.")

    if skip is not None:
        tag: Optional[PickTag] = None
    elif positive and taste >= STRONG_MIN:
        tag = "Strong match"
    else:
        tag = "Something new"
    return " ".join(sentences[:3]), used, tag


# ---------------------------------------------------------------- candidates and scoring


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _candidates() -> list[Event]:
    now = datetime.now(timezone.utc)
    upcoming = [e for e in registry.get_catalog().events() if _aware(e.starts_at) > now]
    return sorted(upcoming, key=lambda e: _aware(e.starts_at))[:MAX_CANDIDATES]


def _fingerprint(items: Iterable[str]) -> str:
    return hashlib.sha1("\n".join(items).encode("utf-8")).hexdigest()


_encoded: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()
_encoded_lock = threading.Lock()


def _ensure_encoded(ranking, events: list[Event]) -> None:
    """Calls ranking.encode_catalog once per catalog (SIE encodes each event once)."""
    fp = _fingerprint(sorted(e.id for e in events))
    with _encoded_lock:
        try:
            if _encoded.get(ranking) == fp:
                return
        except TypeError:
            pass
    try:
        ranking.encode_catalog(events)
    except Exception as exc:
        log.warning("encode_catalog failed: %s", exc)
        return
    with _encoded_lock:
        try:
            _encoded[ranking] = fp
        except TypeError:
            pass


def _score(ranking, taste: str, events: list[Event], arm: Arm) -> dict[str, float]:
    """Ranker scores scaled to 0..1 by the best one ({} when nothing matches)."""
    if not taste.strip() or not events:
        return {}
    try:
        pairs = ranking.score(taste, events, arm)
    except Exception as exc:
        log.warning("ranking.score failed (%s): %s", arm, exc)
        return {}
    top = max((s for _, s in pairs), default=0.0)
    if top <= 0:
        return {}
    return {event_id: max(s, 0.0) / top for event_id, s in pairs}


def _blend(ranking, parts: list[tuple[float, str]], events: list[Event], arm: Arm) -> dict[str, float]:
    """Weighted blend of each taste part's scores, run in parallel. Values are 0..1."""
    parts = [(w, t) for w, t in parts if t.strip()]
    if not parts or not events:
        return {e.id: 0.0 for e in events}
    futures = [(w, _pool.submit(_score, ranking, t, events, arm)) for w, t in parts]
    results = [(w, f.result()) for w, f in futures]
    results = [(w, r) for w, r in results if r]
    total = sum(w for w, _ in results)
    if total <= 0:
        return {e.id: 0.0 for e in events}
    return {e.id: sum(w * r.get(e.id, 0.0) for w, r in results) / total for e in events}


def _taste_parts(user: User, memory: Memory) -> list[tuple[float, str]]:
    try:
        remembered = memory.taste(user.id) or ""
    except Exception as exc:
        log.warning("memory.taste failed: %s", exc)
        remembered = ""
    return [(INTO_WEIGHT, "; ".join(user.into)), (1 - INTO_WEIGHT, remembered)]


def _usual_slots(user: User) -> list[text_jobs.Slot]:
    return [slot for label in user.usual_times for slot in text_jobs.parse_times(label)]


def _in_slots(event: Event, slots: list[text_jobs.Slot]) -> bool:
    return any(text_jobs.slot_matches(slot, _aware(event.starts_at)) for slot in slots)


def _fits_when(event: Event, query: text_jobs.AskQuery) -> bool:
    day = _aware(event.starts_at).astimezone(text_jobs.PT).date()
    if query.date_from and day < query.date_from:
        return False
    if query.date_to and day > query.date_to:
        return False
    if query.slots and not _in_slots(event, query.slots):
        return False
    return not (query.free_only and not event.free)


# ---------------------------------------------------------------- memory recall (cached)

_recall_cache: dict[tuple[str, str, str], list[Fact]] = {}
_recall_lock = threading.Lock()


def _memory_version(memory: Memory, user_id: str) -> Optional[str]:
    """Changes whenever a fact is added or forgotten, so cached recalls never go stale."""
    try:
        groups = memory.facts(user_id)
    except Exception:
        return None
    return _fingerprint(sorted(f.id for f in [*groups.said, *groups.went, *groups.skipped]))


def _recall_one(memory: Memory, user_id: str, event: Event, version: Optional[str]) -> list[Fact]:
    key = (user_id, event.id, version) if version else None
    if key:
        with _recall_lock:
            hit = _recall_cache.get(key)
        if hit is not None:
            return hit
    try:
        facts = list(memory.recall(user_id, event, k=RECALL_K))
    except Exception as exc:
        log.warning("memory.recall failed for %s: %s", event.id, exc)
        return []
    if key:
        with _recall_lock:
            if len(_recall_cache) > 5000:
                _recall_cache.clear()
            _recall_cache[key] = facts
    return facts


def _recall_many(memory: Memory, user_id: str, events: list[Event]) -> dict[str, list[Fact]]:
    version = _memory_version(memory, user_id)
    if RECALL_WORKERS <= 1 or len(events) <= 1:
        return {e.id: _recall_one(memory, user_id, e, version) for e in events}
    futures = {e.id: _pool.submit(_recall_one, memory, user_id, e, version) for e in events}
    return {event_id: f.result() for event_id, f in futures.items()}


# ---------------------------------------------------------------- picks and why lines


@dataclass
class _Row:
    event: Event
    score: float  # final: taste + time boost, times skip penalty
    taste: float  # blended taste score, 0..1
    positive: list[Fact]
    penalized: bool
    in_time: bool


def _rows(events: list[Event], scores: dict[str, float], facts: dict[str, list[Fact]],
          slots: list[text_jobs.Slot]) -> list[_Row]:
    rows = []
    for event in events:
        taste = scores.get(event.id, 0.0)
        in_time = _in_slots(event, slots)
        recalled = facts.get(event.id, [])
        penalized = bool(recalled) and recalled[0].kind in NEGATIVE
        score = (taste + (TIME_BOOST if in_time else 0.0)) * (SKIP_PENALTY if penalized else 1.0)
        positive = [f for f in recalled if f.kind in POSITIVE and _fits_time(f, event)]
        rows.append(_Row(event, score, taste, positive, penalized, in_time))
    rows.sort(key=lambda r: -r.score)  # stable: ties stay soonest first
    return rows


def _make_picks(rows: list[_Row], arm: Arm, weak: PickTag, topic: str = "") -> list[Pick]:
    picks: list[Pick] = []
    used: set[str] = set()
    for row in rows:
        fact = next((f for f in row.positive if f.id not in used), row.positive[0] if row.positive else None)
        why_fact: Optional[Fact] = None
        if fact is not None and not row.penalized and row.taste >= STRONG_MIN:
            tag: PickTag = "Strong match"
            why_fact, why = fact, _fact_sentence(fact)
        elif weak == "Match":
            tag = "Match"
            if fact is not None and not row.penalized:
                why_fact, why = fact, _fact_sentence(fact)
            else:
                why = _plain_match(row.event, topic, row.in_time)
        else:
            tag = "Something new"
            why_fact, why = _something_new(row.event, row.positive, row.in_time)
        if why_fact is not None:
            used.add(why_fact.id)
        picks.append(Pick(event=row.event, score=round(row.score, 4), why=why,
                          why_fact_id=why_fact.id if why_fact else None, tag=tag, arm=arm))
    return picks


_VERBS = {"likes": "like", "loves": "love", "follows": "follow", "prefers": "prefer", "enjoys": "enjoy",
          "goes": "go", "attends": "attend", "joins": "join", "keeps": "keep", "skips": "skip"}
_PAST = ("went", "attended", "joined", "rsvped", "rsvp'd", "skipped", "liked", "loved")


def _fact_sentence(fact: Fact) -> str:
    """Quotes a fact in the canvas's voice, e.g. 'You said climbing and AI agents lately.'"""
    text = fact.text.strip().rstrip(".")
    if fact.kind == "said":
        m = re.match(r"into\s+(.+)", text, flags=re.I)
        return f"You said {m.group(1)}." if m else f"You said “{text}.”"
    first, _, rest = text.partition(" ")
    verb = _VERBS.get(first.lower())
    if verb and rest:
        body = f"You {verb} {rest}"
    elif first.lower() in _PAST:
        body = f"You {_lower_first(text)}"
    else:
        body = text
    source = fact.from_text.strip().rstrip(".")
    return f"{body}: {_lower_first(source)}." if source else f"{body}."


def _when(event: Event) -> str:
    local = _aware(event.starts_at).astimezone(text_jobs.PT)
    part = "morning" if local.hour < 12 else "afternoon" if local.hour < 17 else "evening"
    if local.weekday() >= 5:
        return f"a weekend {part}"
    return "a weeknight" if part == "evening" else f"a weekday {part}"


def _place(event: Event) -> str:
    where = event.neighborhood or event.venue
    return f" in {where}" if where else ""


_TIME_WORDS = {"weekend", "weekday", "weeknight", "morning", "afternoon", "evening", "night"}


def _time_words(text: str) -> set[str]:
    words = {w[:-1] if w.endswith("s") else w for w in re.findall(r"[a-z]+", text.lower())}
    return words & _TIME_WORDS


def _fits_time(fact: Fact, event: Event) -> bool:
    """A fact that names a time of week ("Weekend afternoons stay free") only fits events at that time."""
    named = _time_words(fact.text)
    return not named or named <= _time_words(_when(event))


def _something_new(event: Event, positive: list[Fact], in_time: bool) -> tuple[Optional[Fact], str]:
    """'Something new: a weekend afternoon in Inner Sunset. You said “Weekend afternoons stay free.”'"""
    when = _when(event)
    line = f"Something new: {when}{_place(event)}"
    # Quote a said fact about this time of week (positive facts already fit the event's time).
    said = next((f for f in positive if f.kind == "said" and _time_words(f.text)), None)
    if said is not None:
        return said, f"{line}. You said “{said.text.strip().rstrip('.')}.”"
    if in_time:
        return None, f"{line}, when you usually go."
    return None, f"{line}."


def _plain_match(event: Event, topic: str, in_time: bool) -> str:
    bits = [f"{_when(event)}{_place(event)}"]
    if event.going_count is not None:
        bits.append(f"{event.going_count} going")
    body = ", ".join(bits)
    line = f"Fits {topic}: {body}" if topic else _cap_first(body)
    return line + (", when you usually go." if in_time else ".")


def _event_sentence(event: Event, in_time: bool) -> str:
    blob = f"{event.title} {event.description} {' '.join(event.tags)}".lower()
    traits = []
    if re.search(r"hands-on|hands on|workshop|build night|hack", blob):
        traits.append("hands-on")
    if event.going_count is not None and event.going_count < 150:
        traits.append("small")
    lead = f"This one is {' and '.join(traits)}, " if traits else "This one is "
    line = f"{lead}{_when(event)}{_place(event)}"
    if in_time:
        line += ", when you usually go"
    if event.going_count is not None:
        line += f", with {event.going_count} going"
    return line + "."


# ---------------------------------------------------------------- small text helpers

_NUMBERS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]


def _count(n: int) -> str:
    return _NUMBERS[n] if n < len(_NUMBERS) else str(n)


def _ask_summary(fit: int, left_out: int, query: text_jobs.AskQuery) -> str:
    if fit == 0:
        summary = "Nothing fits that yet."
    else:
        summary = f"{_count(fit)} {'fits' if fit == 1 else 'fit'}."
    if left_out and query.max_going:
        verb = "is" if left_out == 1 else "are"
        said = query.size_phrase or f"under {query.max_going}"
        summary += (f" I left out {_count(left_out).lower()} that {verb} over {query.max_going} people,"
                    f" since you said {said}.")
    return summary


def _cap_first(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _lower_first(text: str) -> str:
    if len(text) > 1 and text[1].isupper():
        return text
    return text[:1].lower() + text[1:]


def _join(tags: list[str]) -> str:
    words = [_lower_first(t) for t in tags]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _merge(new: list[str], old: list[str]) -> list[str]:
    out: list[str] = []
    for tag in [*new, *old]:
        if tag.strip() and tag.lower() not in {t.lower() for t in out}:
            out.append(tag)
    return out


def _today_label() -> str:
    today = datetime.now(text_jobs.PT)
    return f"{today:%b} {today.day}"
