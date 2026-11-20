"""Word-overlap stand-in for SIE and TF-IDF. Same interface as SieRanking."""

from __future__ import annotations

import re

from app.contracts import Arm, Event


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}


class FakeRanking:
    def encode_catalog(self, events: list[Event]) -> None:
        return None

    def score(self, taste: str, candidates: list[Event], arm: Arm) -> list[tuple[str, float]]:
        want = _words(taste)
        out = []
        for event in candidates:
            have = _words(" ".join([event.title, event.description, *event.tags]))
            out.append((event.id, len(want & have) / (len(want) or 1)))
        return sorted(out, key=lambda pair: -pair[1])
