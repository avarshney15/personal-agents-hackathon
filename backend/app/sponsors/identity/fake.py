"""Stand-in for Persona: every inquiry passes. Same interface as PersonaIdentity."""

from __future__ import annotations

import uuid
from typing import Optional

from app.contracts import DISCLOSURE_TEMPLATE, User, VerifySession


class FakeIdentity:
    def __init__(self) -> None:
        self._started: set[str] = set()

    def start(self, user: User) -> VerifySession:
        self._started.add(user.id)
        return VerifySession(inquiry_id=f"inq_fake_{uuid.uuid4().hex[:8]}")

    def status(self, user: User) -> bool:
        return user.id in self._started or user.verified

    def on_webhook(self, payload: dict) -> Optional[tuple[str, bool]]:
        user_id = payload.get("user_id")
        return (user_id, True) if user_id else None

    def disclosure(self, user: User) -> str:
        return DISCLOSURE_TEMPLATE.format(name=user.name)
