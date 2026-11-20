"""Stand-in for Persona: every inquiry passes. Same interface as PersonaIdentity."""

from __future__ import annotations

import uuid
from typing import Optional

from app.contracts import DISCLOSURE_TEMPLATE, User, VerifySession
from app.sponsors.identity import webhook


class FakeIdentity:
    def __init__(self) -> None:
        self._started: set[str] = set()

    def start(self, user: User) -> VerifySession:
        self._started.add(user.id)
        return VerifySession(inquiry_id=f"inq_fake_{uuid.uuid4().hex[:8]}")

    def status(self, user: User) -> bool:
        return user.id in self._started or user.verified

    def on_webhook(self, payload: dict, raw_body: Optional[webhook.Body] = None,
                   signature: Optional[str] = None) -> Optional[tuple[str, bool]]:
        event, _, _ = webhook.unwrap(payload, raw_body, signature)
        if not event:
            return None
        if event.get("user_id"):
            return event["user_id"], True
        parsed = webhook.parse_inquiry_event(event)  # a real Persona-shaped body
        if parsed is None:
            return None
        name, _, status, reference_id = parsed
        verified = webhook.verdict(name, status)
        return (reference_id, verified) if reference_id and verified is not None else None

    def disclosure(self, user: User) -> str:
        return DISCLOSURE_TEMPLATE.format(name=user.name)
