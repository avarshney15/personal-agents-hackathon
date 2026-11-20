"""Stand-in for Amplitude + Statsig: keeps events in memory, hashes users into arms."""

from __future__ import annotations

import contextlib
import hashlib
import logging
from typing import Iterator, Optional

from app.contracts import Arm

log = logging.getLogger("telemetry")


def hash_arm(user_id: str, experiment: str = "ranking") -> Arm:
    """Deterministic 50/50 split. LiveTelemetry falls back to this when Statsig can't answer."""
    digest = hashlib.sha256(f"{experiment}:{user_id}".encode()).digest()
    return "sie" if digest[0] % 2 == 0 else "tfidf"


class FakeTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def wrap(self, client):
        return client

    def track(self, user_id: str, event: str, props: Optional[dict] = None) -> None:
        self.events.append((user_id, event, props or {}))
        log.info("track %s %s %s", user_id, event, props or {})

    def arm(self, user_id: str, experiment: str = "ranking") -> Arm:
        return hash_arm(user_id, experiment)

    def flush(self) -> None:
        return None

    @contextlib.contextmanager
    def run(self, user_id: str, run_id: str, arm: Optional[Arm] = None,
            event_id: Optional[str] = None) -> Iterator[None]:
        """Optional: groups the Claude calls of one RSVP run. No-op here; see LiveTelemetry.run."""
        yield None
