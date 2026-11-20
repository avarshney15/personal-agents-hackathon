"""Reads a public Luma event page into an Event. Owned by Part 5. No login.

Luma pages embed the event as JSON in `<script id="__NEXT_DATA__">`; older or cached pages
may carry schema.org JSON-LD instead. Both are handled.

    python -m app.catalog.luma https://luma.com/bakeoff          # print the Event as JSON
    python -m app.catalog.luma --add https://luma.com/bakeoff    # also add it to data/events.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from app.contracts import Event

PACIFIC = ZoneInfo("America/Los_Angeles")
LUMA_HOSTS = {"lu.ma", "www.lu.ma", "luma.com", "www.luma.com"}
_UA = "Mozilla/5.0 (compatible; EventAgent/0.1; +https://luma.com)"

# Keyword -> tag. Tags feed ranking and the "why" line, so they follow the canvas wording.
_TAG_RULES: list[tuple[str, str]] = [
    (r"\bagent(s|ic)?\b|\bagenthon\b", "AI agents"),
    (r"\b(ai|llms?|gpt|claude|genai|machine learning|ml|mcp)\b", "AI"),
    (r"\b(hackathon|hack|hacks|agenthon|breakathon|build day)\b", "hackathon"),
    (r"\b(workshop|hands-on|builders? night|build night|bootcamp|class)\b", "workshop"),
    (r"\b(climb|climbing|climbers?|boulder|bouldering)\b", "climbing"),
    (r"\b(art|arts|artists?|paint|painting|ceramics?|pottery|clay|crafts?|drawing|zines?|mosaic|"
     r"printing|embossing|wreath|floral|candle)\b", "art"),
    (r"\b(robots?|robotics|embodied|hardware|physical ai)\b", "hardware"),
    (r"\b(demo day|demo night|demos?)\b", "demos"),
    (r"\b(talks?|fireside|panel|speakers?)\b", "talks"),
    (r"\b(games?|playtesting|game night)\b", "games"),
    (r"\b(run club|5k|turkey trot|hot trot|fitness|yoga|workout)\b", "fitness"),
    (r"\b(market|holiday party|party|social|mixer|happy hour)\b", "social"),
    (r"\b(film|films|movie|screening|documentary)\b", "film"),
    (r"\b(founders?|startups?|gtm|saas|vc|venture)\b", "startups"),
    (r"\b(books?|book swap)\b", "books"),
]


def guess_tags(title: str, description: str = "", categories: Optional[list[str]] = None,
               starts_at: Optional[datetime] = None) -> list[str]:
    text = f"{title}\n{description}".lower()
    tags: list[str] = []
    for pattern, tag in _TAG_RULES:
        if re.search(pattern, text) and tag not in tags:
            tags.append(tag)
    if any(c.lower() == "ai" for c in categories or []) and "AI" not in tags:
        tags.append("AI")
    if starts_at and starts_at.astimezone(PACIFIC).weekday() >= 5:
        tags.append("weekend")
    return tags


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else ""


def is_luma_url(url: str) -> bool:
    return urlparse(url).netloc.lower() in LUMA_HOSTS and bool(slug_from_url(url))


def fetch_html(url: str, timeout: float = 15.0) -> str:
    resp = httpx.get(url, follow_redirects=True, timeout=timeout, headers={"User-Agent": _UA})
    resp.raise_for_status()
    return resp.text


def _flatten_mirror(node: Any) -> str:
    """ProseMirror doc -> plain text, one line per block."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        if node.get("type") == "hard_break":
            return "\n"
        inner = "".join(_flatten_mirror(c) for c in node.get("content") or [])
        block = node.get("type") in ("paragraph", "heading", "list_item", "bullet_list", "ordered_list")
        return inner + ("\n" if block else "")
    if isinstance(node, list):
        return "".join(_flatten_mirror(c) for c in node)
    return ""


def short_description(text: str, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def _money(cents: Optional[int]) -> str:
    if cents is None:
        return ""
    return f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"


def price_text(ticket: dict) -> str:
    """"Free", "$25", "$10 to $50", or "From $10" for pay-what-you-want."""
    if ticket.get("is_free", True) and not ticket.get("max_price"):
        return "Free"
    price = ticket.get("price") or {}
    low = _money(price.get("min_cents") if price.get("is_flexible") else price.get("cents"))
    high = _money((ticket.get("max_price") or {}).get("cents"))
    if ticket.get("is_free") and high:
        return f"Free to {high}"
    if price.get("is_flexible"):
        return f"From {low or '$0'}"
    return f"{low} to {high}" if low and high and low != high else (low or high or "Paid")


def _next_data(html: str) -> Optional[dict]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["initialData"]["data"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("event"), dict) else None


def _json_ld(html: str) -> Optional[dict]:
    for raw in re.findall(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in doc if isinstance(doc, list) else [doc]:
            if isinstance(item, dict) and "Event" in str(item.get("@type", "")):
                return item
    return None


def event_from_next_data(data: dict, url: str) -> Event:
    ev = data["event"]
    geo = ev.get("geo_address_info") or {}
    ticket = data.get("ticket_info") or {}
    calendar = data.get("calendar") or {}
    hosts = data.get("hosts") or []
    host = calendar.get("name") if calendar.get("name") and not calendar.get("is_personal") else ""
    if not host and hosts:
        host = hosts[0].get("name", "")
    description = short_description(_flatten_mirror(data.get("description_mirror")))
    categories = [c.get("name", "") for c in data.get("categories") or []]
    slug = ev.get("url") or slug_from_url(url)
    starts_at = datetime.fromisoformat(ev["start_at"].replace("Z", "+00:00"))
    neighborhood = geo.get("sublocality") or ""
    venue = geo.get("address") or geo.get("full_address") or neighborhood
    going = data.get("guest_count")
    spots = ticket.get("spots_remaining")
    if ticket.get("is_sold_out"):
        spots = 0
    return Event(
        id=slug,
        title=ev["name"].strip(),
        starts_at=starts_at,
        ends_at=datetime.fromisoformat(ev["end_at"].replace("Z", "+00:00")) if ev.get("end_at") else None,
        venue=venue,
        neighborhood=neighborhood,
        url=f"https://luma.com/{slug}",
        source="luma",
        host=host,
        description=description,
        free=bool(ticket.get("is_free", True)),
        spots_left=spots,
        has_questions=bool(data.get("registration_questions")),
        tags=guess_tags(ev["name"], description, categories, starts_at),
        site="luma",
        going_count=going if isinstance(going, int) and going > 0 else None,
        price_text=price_text(ticket),
    )


def event_from_json_ld(item: dict, url: str) -> Event:
    loc = item.get("location") or {}
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address") if isinstance(loc, dict) else None
    org = item.get("organizer") or {}
    org = org[0] if isinstance(org, list) and org else org
    offers = item.get("offers") or {}
    offers = offers[0] if isinstance(offers, list) and offers else offers
    price = str(offers.get("price", "0")) if isinstance(offers, dict) else "0"
    description = short_description(item.get("description") or "")
    slug = slug_from_url(url)
    starts_at = datetime.fromisoformat(item["startDate"].replace("Z", "+00:00"))
    return Event(
        id=slug,
        title=(item.get("name") or slug).strip(),
        starts_at=starts_at,
        ends_at=datetime.fromisoformat(item["endDate"].replace("Z", "+00:00")) if item.get("endDate") else None,
        venue=(loc.get("name") if isinstance(loc, dict) else "") or "",
        neighborhood=(addr.get("addressLocality") if isinstance(addr, dict) else "") or "",
        url=f"https://luma.com/{slug}",
        source="luma",
        host=(org.get("name") if isinstance(org, dict) else "") or "",
        description=description,
        free=price in ("0", "0.0", "0.00", ""),
        tags=guess_tags(item.get("name") or "", description, starts_at=starts_at),
        site="luma",
    )


def parse_event_html(html: str, url: str) -> Event:
    data = _next_data(html)
    if data:
        return event_from_next_data(data, url)
    item = _json_ld(html)
    if item:
        return event_from_json_ld(item, url)
    raise ValueError(f"No event data found on {url}")


def read_event(url: str) -> Event:
    """Fetch a public Luma event page and parse it. Raises on network or parse errors."""
    if not url.startswith("http"):
        url = f"https://luma.com/{url.strip('/')}"
    if not is_luma_url(url):
        raise ValueError(f"Not a Luma event URL: {url}")
    return parse_event_html(fetch_html(url), url)


def _main(argv: list[str]) -> int:
    add = "--add" in argv
    urls = [a for a in argv if not a.startswith("--")]
    if not urls:
        print(__doc__)
        return 2
    for url in urls:
        event = read_event(url)
        print(event.model_dump_json(indent=2))
        if add:
            from app.catalog.seed import add_event
            add_event(event)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
