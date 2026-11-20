"""Seed catalog and Luma reader tests (Part 5). Offline unless LUMA_LIVE=1."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from app.catalog import luma
from app.catalog.seed import SeedCatalog, add_event, events_path, load_events
from app.contracts import Catalog, Event


def test_events_json_loads_through_seed_catalog():
    catalog = SeedCatalog()
    assert isinstance(catalog, Catalog)
    raw = load_events()
    assert 30 <= len(raw) <= 40
    assert len({e.id for e in raw}) == len(raw)
    for event in raw:
        assert event.source == "luma" and event.site == "luma"
        assert event.url == f"https://luma.com/{event.id}"
        assert event.title and event.starts_at.tzinfo and event.price_text
    upcoming = catalog.events()
    assert upcoming == sorted(upcoming, key=lambda e: e.starts_at)
    assert all(e.starts_at > datetime.now(timezone.utc) for e in upcoming)
    assert catalog.get(raw[0].id) == raw[0]
    assert catalog.get("nope") is None


def test_catalog_has_the_demo_mix():
    tags = [set(e.tags) for e in load_events()]
    assert sum("AI agents" in t for t in tags) >= 5
    assert sum("hackathon" in t for t in tags) >= 5
    assert any("workshop" in t for t in tags)
    assert any({"art", "weekend"} <= t for t in tags)


def _write(path, events):
    path.write_text(json.dumps([e.model_dump(mode="json") for e in events]))


def test_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_PATH", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        SeedCatalog()


def test_only_upcoming_sorted_but_get_finds_past(tmp_path):
    now = datetime.now(timezone.utc)
    mk = lambda i, days: Event(id=f"e{i}", title=f"E{i}", starts_at=now + timedelta(days=days),
                               url=f"https://luma.com/e{i}", source="luma")
    path = tmp_path / "events.json"
    _write(path, [mk(1, 3), mk(2, -1), mk(3, 1)])
    catalog = SeedCatalog(path)
    assert [e.id for e in catalog.events()] == ["e3", "e1"]
    assert catalog.get("e2").title == "E2"


def test_add_event_inserts_sorted(tmp_path):
    path = tmp_path / "events.json"
    now = datetime.now(timezone.utc)
    add_event(Event(id="b", title="B", starts_at=now + timedelta(days=2), url="https://luma.com/b"), path)
    add_event(Event(id="a", title="A", starts_at=now + timedelta(days=1), url="https://luma.com/a"), path)
    add_event(Event(id="b", title="B2", starts_at=now + timedelta(days=2), url="https://luma.com/b"), path)
    assert [(e.id, e.title) for e in load_events(path)] == [("a", "A"), ("b", "B2")]


# ---------------------------------------------------------------- Luma reader

_NEXT = {
    "props": {"pageProps": {"initialData": {"kind": "event", "data": {
        "event": {"name": "Agent builders night ", "start_at": "2026-12-05T02:30:00.000Z",
                  "end_at": "2026-12-05T05:00:00.000Z", "url": "abc123",
                  "geo_address_info": {"city": "San Francisco", "sublocality": "SoMa",
                                       "address": "123 Folsom St"}},
        "calendar": {"name": "Agent Builders SF", "is_personal": False},
        "hosts": [{"name": "Dev"}],
        "ticket_info": {"is_free": True, "spots_remaining": 12, "is_sold_out": False},
        "registration_questions": [{"label": "What are you building?"}],
        "guest_count": 80,
        "categories": [{"name": "AI"}],
        "description_mirror": {"type": "doc", "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "A hands-on night for people building AI agents."}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Bring a laptop."}]}]},
    }}}},
}


def _page(next_data=None, ld=None) -> str:
    parts = ["<html><head>"]
    if ld:
        parts.append(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    if next_data:
        parts.append(f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script>')
    return "".join(parts) + "</head></html>"


def test_parse_next_data():
    event = luma.parse_event_html(_page(_NEXT), "https://lu.ma/abc123")
    assert event.id == "abc123" and event.url == "https://luma.com/abc123"
    assert event.title == "Agent builders night" and event.source == "luma"
    assert event.neighborhood == "SoMa" and event.venue == "123 Folsom St"
    assert event.host == "Agent Builders SF"
    assert event.free and event.price_text == "Free" and event.spots_left == 12
    assert event.has_questions and event.going_count == 80
    assert event.description.startswith("A hands-on night") and "Bring a laptop." in event.description
    assert {"AI agents", "AI", "workshop"} <= set(event.tags)
    assert "weekend" not in event.tags  # Friday evening in SF


def test_parse_json_ld_fallback():
    ld = {"@context": "https://schema.org", "@type": "Event", "name": "Bouldering social",
          "startDate": "2026-12-06T03:00:00Z", "location": {"name": "Dogpatch Boulders",
          "address": {"addressLocality": "San Francisco"}}, "organizer": {"name": "SF Climbers"},
          "offers": {"price": "0"}, "description": "Climbing meetup."}
    event = luma.parse_event_html(_page(ld=ld), "https://luma.com/boulder1")
    assert event.id == "boulder1" and event.venue == "Dogpatch Boulders" and event.free
    assert "climbing" in event.tags and "weekend" in event.tags  # Saturday 7 pm in SF


def test_parse_no_data_raises():
    with pytest.raises(ValueError):
        luma.parse_event_html("<html></html>", "https://luma.com/x")


def test_price_text():
    assert luma.price_text({"is_free": True}) == "Free"
    assert luma.price_text({"is_free": False, "price": {"cents": 2500}}) == "$25"
    assert luma.price_text({"is_free": False, "price": {"cents": 1000}, "max_price": {"cents": 5000}}) == "$10 to $50"
    assert luma.price_text({"is_free": True, "max_price": {"cents": 1000}}) == "Free to $10"
    assert luma.price_text({"is_free": False, "price": {"cents": 1000, "min_cents": 0, "is_flexible": True}}) == "From $0"


def test_luma_urls():
    assert luma.is_luma_url("https://lu.ma/abc") and luma.is_luma_url("https://luma.com/abc?tk=1")
    assert not luma.is_luma_url("https://example.com/abc")
    assert luma.slug_from_url("https://luma.com/abc/") == "abc"


@pytest.mark.skipif(os.getenv("LUMA_LIVE") != "1", reason="set LUMA_LIVE=1 to hit luma.com")
def test_read_event_live():
    first = load_events(events_path())[0]
    event = luma.read_event(first.url)
    assert event.id == first.id and event.title
