"""Stand-in for Amplitude + Statsig: keeps events in memory, hashes users into arms."""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from app.contracts import Arm

log = logging.getLogger("telemetry")


class FakeTelemetry:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def wrap(self, client):
        return client

    def track(self, user_id: str, event: str, props: Optional[dict] = None) -> None:
        self.events.append((user_id, event, props or {}))
        log.info("track %s %s %s", user_id, event, props or {})

    def arm(self, user_id: str, experiment: str = "ranking") -> Arm:
        digest = hashlib.sha256(f"{experiment}:{user_id}".encode()).digest()
        return "sie" if digest[0] % 2 == 0 else "tfidf"

    def flush(self) -> None:
        return None
