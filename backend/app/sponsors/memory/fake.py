"""In-memory stand-in for Cognee. Same interface as CogneeMemory; needs no keys."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.contracts import Event, Fact, FactGroups, FactKind
from app.sponsors.memory.text_match import overlap


class FakeMemory:
    def __init__(self) -> None:
        self._facts: dict[str, dict[str, Fact]] = {}

    def remember(self, user_id: str, kind: FactKind, text: str, from_text: str,
                 event_id: Optional[str] = None) -> str:
        fact = Fact(id=uuid.uuid4().hex[:12], kind=kind, text=text.strip(),
                    from_text=from_text.strip(), event_id=event_id,
                    created_at=datetime.now(timezone.utc))
        self._facts.setdefault(user_id, {})[fact.id] = fact
        return fact.id

    def recall(self, user_id: str, event: Event, k: int = 3) -> list[Fact]:
        """Facts sharing the most words with the event, ties newest first."""
        scored = [(overlap(f.text, event), f) for f in self._newest_first(user_id)]
        scored = sorted((p for p in scored if p[0] > 0), key=lambda p: -p[0])
        return [f for _, f in scored[:k]]

    def recall_many(self, user_id: str, events: Iterable[Event], k: int = 3) -> dict[str, list[Fact]]:
        return {e.id: self.recall(user_id, e, k) for e in events}

    def facts(self, user_id: str) -> FactGroups:
        groups = FactGroups()
        for fact in self._newest_first(user_id):
            bucket = "skipped" if fact.kind == "hidden" else fact.kind
            getattr(groups, bucket).append(fact)
        return groups

    def forget(self, user_id: str, fact_id: str) -> None:
        self._facts.get(user_id, {}).pop(fact_id, None)

    def forget_all(self, user_id: str) -> None:
        self._facts.pop(user_id, None)

    def taste(self, user_id: str) -> str:
        facts = self._newest_first(user_id)
        said = [f.text for f in facts if f.kind == "said"]
        went = [f.text for f in facts if f.kind == "went"]
        return "; ".join(said + went)

    def _newest_first(self, user_id: str) -> list[Fact]:
        facts = list(self._facts.get(user_id, {}).values())  # insertion order
        return sorted(reversed(facts), key=lambda f: f.created_at, reverse=True)
