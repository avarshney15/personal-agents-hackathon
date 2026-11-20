"""In-memory stand-in for Cognee. Same interface as CogneeMemory."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.contracts import Event, Fact, FactGroups, FactKind


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


class FakeMemory:
    def __init__(self) -> None:
        self._facts: dict[str, dict[str, Fact]] = {}

    def remember(self, user_id: str, kind: FactKind, text: str, from_text: str,
                 event_id: Optional[str] = None) -> str:
        fact = Fact(id=uuid.uuid4().hex[:12], kind=kind, text=text, from_text=from_text,
                    event_id=event_id, created_at=datetime.now(timezone.utc))
        self._facts.setdefault(user_id, {})[fact.id] = fact
        return fact.id

    def recall(self, user_id: str, event: Event, k: int = 3) -> list[Fact]:
        target = _words(" ".join([event.title, event.description, event.host, *event.tags]))
        scored = [(len(_words(f.text) & target), f) for f in self._facts.get(user_id, {}).values()]
        scored = [(s, f) for s, f in scored if s > 0]
        scored.sort(key=lambda pair: (-pair[0], pair[1].created_at), reverse=False)
        return [f for _, f in scored[:k]]

    def facts(self, user_id: str) -> FactGroups:
        groups = FactGroups()
        for fact in sorted(self._facts.get(user_id, {}).values(), key=lambda f: f.created_at, reverse=True):
            bucket = "skipped" if fact.kind == "hidden" else fact.kind
            getattr(groups, bucket).append(fact)
        return groups

    def forget(self, user_id: str, fact_id: str) -> None:
        self._facts.get(user_id, {}).pop(fact_id, None)

    def forget_all(self, user_id: str) -> None:
        self._facts.pop(user_id, None)

    def taste(self, user_id: str) -> str:
        facts = [f for f in self._facts.get(user_id, {}).values() if f.kind in ("said", "went")]
        return "; ".join(f.text for f in facts)
