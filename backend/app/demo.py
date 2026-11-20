"""Demo user and the starting memories shown in the canvas. Owned by the lead."""

from __future__ import annotations

import os

from app.contracts import DEMO_USER_ID, FactKind, Memory, User

DEMO_FACTS: list[tuple[FactKind, str, str]] = [
    ("said", "Weekend afternoons stay free", "You told the agent, Oct 2"),
    ("went", "Likes hands-on hackathons over talks", "3 hackathons went, 2 talks skipped"),
    ("went", "Follows hosts from Agent Builders SF", "Went to their last two events"),
    ("skipped", "Not into sales and pitch nights", "Hid 4 of them"),
]


def demo_user() -> User:
    return User(id=DEMO_USER_ID, name=os.getenv("DEMO_USER_NAME", "Maya R."))


def seed_demo_memory(memory: Memory, user_id: str = DEMO_USER_ID) -> None:
    """Adds the canvas's starting facts if the user has none yet."""
    groups = memory.facts(user_id)
    if groups.said or groups.went or groups.skipped:
        return
    for kind, text, from_text in DEMO_FACTS:
        memory.remember(user_id, kind, text, from_text)
