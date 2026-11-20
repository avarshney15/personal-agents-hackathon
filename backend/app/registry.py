"""Picks the real or fake implementation of each module. Owned by the lead.

SPONSORS_FAKE controls the four sponsor modules:
  "1" (default) every sponsor on its fake · "0" every sponsor real ·
  "memory,identity" only those on their fakes.
ACT_FAKE=1 (default) simulates RSVPs; ACT_FAKE=0 drives hosted Chrome.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

from app.contracts import Catalog, Identity, Memory, Ranking, RsvpAgent, Telemetry

load_dotenv()


def use_fake(module: str) -> bool:
    flag = os.getenv("SPONSORS_FAKE", "1").strip().lower()
    if flag in ("1", "true", "all", "yes"):
        return True
    if flag in ("0", "false", "none", "no", ""):
        return False
    return module in {m.strip() for m in flag.split(",")}


@lru_cache
def get_memory() -> Memory:
    if use_fake("memory"):
        from app.sponsors.memory.fake import FakeMemory
        return FakeMemory()
    from app.sponsors.memory.cognee_memory import CogneeMemory
    return CogneeMemory()


@lru_cache
def get_ranking() -> Ranking:
    if use_fake("ranking"):
        from app.sponsors.ranking.fake import FakeRanking
        return FakeRanking()
    from app.sponsors.ranking.sie_ranking import SieRanking
    return SieRanking()


@lru_cache
def get_identity() -> Identity:
    if use_fake("identity"):
        from app.sponsors.identity.fake import FakeIdentity
        return FakeIdentity()
    from app.sponsors.identity.persona_identity import PersonaIdentity
    return PersonaIdentity()


@lru_cache
def get_telemetry() -> Telemetry:
    if use_fake("telemetry"):
        from app.sponsors.telemetry.fake import FakeTelemetry
        return FakeTelemetry()
    from app.sponsors.telemetry.live import LiveTelemetry
    return LiveTelemetry()


@lru_cache
def get_catalog() -> Catalog:
    try:
        from app.catalog.seed import SeedCatalog
        return SeedCatalog()
    except (ImportError, FileNotFoundError):
        from app.catalog.fake import FakeCatalog
        return FakeCatalog()


@lru_cache
def get_rsvp_agent() -> RsvpAgent:
    if os.getenv("ACT_FAKE", "1").strip() in ("1", "true", "yes"):
        from app.act.fake import FakeRsvpAgent
        return FakeRsvpAgent()
    from app.act.browser_agent import BrowserRsvpAgent
    return BrowserRsvpAgent()
