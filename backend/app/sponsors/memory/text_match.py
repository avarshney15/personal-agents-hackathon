"""Plain-text helpers shared by FakeMemory and CogneeMemory's keyword fallback."""

from __future__ import annotations

import re
from datetime import timezone
from zoneinfo import ZoneInfo

from app.contracts import Event

_PT = ZoneInfo("America/Los_Angeles")

_STOP = {
    "and", "the", "for", "from", "with", "into", "over", "than", "that", "this", "these",
    "those", "you", "your", "our", "their", "them", "they", "are", "was", "were", "has",
    "have", "had", "but", "not", "any", "all", "out", "about", "who", "what", "when",
    "where", "why", "how", "its", "his", "her", "lately", "likes", "like", "stay", "stays",
    "people", "night", "nights", "event", "events", "last", "two", "three", "went", "said",
    "told",
}


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(text: str) -> set[str]:
    """Lowercase content words, lightly stemmed ("hackathons" -> "hackathon")."""
    out = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) <= 2 or w in _STOP:
            continue
        out.add(_stem(w))
    return out


def when_words(event: Event) -> str:
    """'Saturday afternoon, weekend' so facts about time can match."""
    start = event.starts_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    local = start.astimezone(_PT)
    part = "morning" if local.hour < 12 else "afternoon" if local.hour < 17 else "evening"
    day = local.strftime("%A")
    span = "weekend" if local.weekday() >= 5 else "weekday"
    return f"{day} {part}, {span}"


def event_text(event: Event) -> str:
    """One query string describing the event, used for search by meaning and keywords."""
    parts = [event.title]
    if event.description:
        parts.append(event.description)
    if event.host:
        parts.append(f"Hosted by {event.host}")
    if event.tags:
        parts.append("Tags: " + ", ".join(event.tags))
    where = event.neighborhood or event.venue
    if where:
        parts.append(f"In {where}")
    parts.append(when_words(event))
    return ". ".join(p.strip().rstrip(".") for p in parts if p.strip()) + "."


def overlap(fact_text: str, event: Event) -> int:
    return len(words(fact_text) & words(event_text(event)))
