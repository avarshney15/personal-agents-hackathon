"""Text jobs: tag events and read what the user is into. Owned by Part 2.

Keyword fallback below keeps everything running without an API key.
Part 2 adds the Claude path (and Arcee in stretch) behind the same two functions.
"""

from __future__ import annotations

import re

from app.contracts import Event, Intent


def tag_events(events: list[Event]) -> dict[str, list[str]]:
    return {e.id: list(e.tags) for e in events}


def read_intent(text: str) -> Intent:
    parts = [p.strip() for p in re.split(r",| and |;|\.", text) if p.strip()]
    parts = [re.sub(r"^(i'?m |into |lately |really )+", "", p, flags=re.I) for p in parts]
    parts = [re.sub(r"\b(lately|right now|these days)\b", "", p, flags=re.I).strip() for p in parts]
    tags = [p[:1].upper() + p[1:] for p in parts if p]
    return Intent(taste="; ".join(tags), tags=tags)
