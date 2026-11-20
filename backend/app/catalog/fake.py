"""The four events drawn in the canvas's For you screen. Used until data/events.json exists."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.contracts import Event


def _at(days: int, hour: int, minute: int = 0) -> datetime:
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return base + timedelta(days=days, hours=hour + 8, minutes=minute)  # PT as UTC-8


_EVENTS = [
    Event(id="fake-agent-builders", title="Agent builders night", starts_at=_at(2, 18, 30),
          venue="SoMa", neighborhood="SoMa", url="https://lu.ma/", host="Agent Builders SF",
          description="Hands-on night for people building AI agents.", tags=["AI agents", "hackathon"]),
    Event(id="fake-bouldering", title="Bouldering social", starts_at=_at(4, 19),
          venue="Dogpatch Boulders", neighborhood="Dogpatch", url="https://lu.ma/",
          description="Climbing meetup and social.", tags=["climbing", "social"]),
    Event(id="fake-evals-workshop", title="Evals for agents, a workshop", starts_at=_at(6, 10),
          venue="Mission", neighborhood="Mission", url="https://lu.ma/", host="Agent Builders SF",
          description="Hands-on workshop on evaluating AI agents.", tags=["AI agents", "workshop"]),
    Event(id="fake-ceramics", title="Ceramics for beginners", starts_at=_at(7, 14),
          venue="Inner Sunset", neighborhood="Inner Sunset", url="https://lu.ma/",
          description="A weekend afternoon at the wheel.", tags=["art", "weekend"]),
]


class FakeCatalog:
    def events(self) -> list[Event]:
        return list(_EVENTS)

    def get(self, event_id: str) -> Optional[Event]:
        return next((e for e in _EVENTS if e.id == event_id), None)
