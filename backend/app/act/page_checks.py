"""Deterministic reads of an event page: is it free, open, and question-free; are we signed in;
does the page say we're going. All best-effort and tolerant of markup changes."""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from app.contracts import Event

SCAN_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelFor = f => {
    let t = f.getAttribute('aria-label') || f.getAttribute('placeholder') || '';
    if (f.id) {
      const l = document.querySelector(`label[for="${CSS.escape(f.id)}"]`);
      if (l) t = l.innerText + ' ' + t;
    }
    const wrap = f.closest('label');
    if (wrap) t = wrap.innerText + ' ' + t;
    return t.trim();
  };
  const buttons = [...document.querySelectorAll('button, a, [role=button], input[type=submit]')]
    .filter(visible)
    .map(b => (b.innerText || b.value || b.getAttribute('aria-label') || '').trim())
    .filter(Boolean).slice(0, 300);
  const fields = [...document.querySelectorAll('input, textarea, select')]
    .filter(visible)
    .filter(f => !['hidden', 'submit', 'button', 'search', 'checkbox', 'radio']
      .includes((f.type || '').toLowerCase()))
    .map(f => ({tag: f.tagName.toLowerCase(), type: (f.type || '').toLowerCase(),
                name: f.name || '', label: labelFor(f), required: !!f.required,
                value: (f.value || '')}));
  const editable = [...document.querySelectorAll('[contenteditable=""], [contenteditable=true]')]
    .map(e => e.innerText || '');
  const next = document.getElementById('__NEXT_DATA__');
  return {
    url: location.href,
    title: document.title,
    text: ((document.body && document.body.innerText) || '').slice(0, 30000),
    buttons,
    fields,
    editable,
    ld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent),
    next: next ? next.textContent : null,
    frames: [...document.querySelectorAll('iframe')].map(f => f.src || ''),
    // Luma's own sign-in links only: hosts often link their own member logins in the event text.
    signin_links: [...document.querySelectorAll('a[href*="signin"], a[href*="/login"]')]
      .filter(a => /(^|\.)(lu\.ma|luma\.com)$/i.test(a.hostname))
      .filter(visible).length,
  };
}
"""

# Short status lines and button labels only: whole-page text holds event descriptions, where
# "If you're in SF" or "sold out last year" are common and mean nothing.
PAID_BUTTON = re.compile(r"^((get|buy) tickets?|check ?out|purchase|pay( now)?)$", re.I)
REGISTER_BUTTON = re.compile(
    r"^(one[- ]click rsvp|rsvp|register|register now|request to join|join event|reserve( a)? spot"
    r"|attend|sign up|accept invite)\b", re.I)  # invite-only events show "Accept Invite"
FULL_LINE = re.compile(
    r"^(sold out|event (is )?full|(this event is )?at capacity|registration (is )?closed"
    r"|no longer accepting (registrations|guests))[.!]?$", re.I)
GOING_LINE = re.compile(
    r"^(you(?:'|’| a)re (?:going|registered|in|on the (?:wait ?list|guest list))"
    r"|registration confirmed|pending(?: host)? approval|you(?:'|’)ve registered)[.!]?$", re.I)
WAITLIST_BUTTON = re.compile(r"^join (the )?wait ?list\b", re.I)
CODE_TEXT = re.compile(
    r"enter (the|your) (\w+[- ])?(digit )?(verification |security |login |sign[- ]in )?code"
    r"|we (just )?(sent|emailed|texted) (you )?(a|an|the) (\w+[- ])*code"
    r"|check your (email|inbox|phone) for (a|the) code|verification code", re.I)
SIGNIN_BUTTON = re.compile(r"^(sign in|log in|sign in to register|sign in with)\b", re.I)
CAPTCHA_FRAME = re.compile(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare\.com|arkoselabs",
                           re.I)
CAPTCHA_TEXT = re.compile(r"verify (that )?you are (a )?human|i'?m not a robot|are you a robot", re.I)
NOTE_FIELD = re.compile(r"\bnote\b|message (to|for) (the )?host|comment", re.I)
IDENTITY_FIELD = re.compile(r"\bname\b|e-?mail|\bsearch\b|phone", re.I)

STOP_PAID = "It's a paid event. The agent never pays, so get the ticket yourself on Luma."
STOP_FULL = "No spots left, and Luma offers no waitlist for this one."
STOP_QUESTIONS = ("The host asks registration questions. The agent never answers for you, "
                  "so register yourself on Luma.")
STOP_CAPTCHA = "Luma showed a human check. Finish it yourself on Luma."
STOP_SIGNIN = "Sign in to Luma in hosted Chrome (run scripts/login_luma.py)"


@dataclass
class EventCheck:
    ok: bool
    reason: Optional[str] = None  # plain stop reason when not ok
    already_going: Optional[str] = None  # status text when the page already shows we're in
    inconclusive: bool = False  # no blocker found but no register button either
    waitlist: bool = False  # full, but Luma offers a waitlist: join it


def _bfs(obj: Any) -> Iterator[dict]:
    queue = deque([obj])
    while queue:
        o = queue.popleft()
        if isinstance(o, dict):
            yield o
            queue.extend(o.values())
        elif isinstance(o, list):
            queue.extend(o)


def _first_with(data: Any, key: str) -> Optional[dict]:
    for d in _bfs(data):
        if key in d:
            return d
    return None


def _ld_blocker(ld_blobs: list[str]) -> Optional[str]:
    """STOP_PAID or STOP_FULL from schema.org offers, else None."""
    offers: list[dict] = []
    for blob in ld_blobs or []:
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        for d in _bfs(data):
            if d.get("@type") in ("Offer", "AggregateOffer"):
                offers.append(d)
    if not offers:
        return None
    if all("soldout" in str(o.get("availability", "")).lower() for o in offers):
        return STOP_FULL
    prices = []
    for o in offers:
        try:
            prices.append(float(o.get("price", o.get("lowPrice", 0)) or 0))
        except (TypeError, ValueError):
            prices.append(0.0)
    if prices and all(p > 0 for p in prices):
        return STOP_PAID
    return None


def _luma_data(next_blob: Optional[str]) -> tuple[Optional[dict], Optional[list]]:
    """Luma's own event data from __NEXT_DATA__: (ticket_info, registration_questions)."""
    if not next_blob:
        return None, None
    try:
        data = json.loads(next_blob)
    except (ValueError, TypeError):
        return None, None
    info = _first_with(data, "ticket_info")
    holder = _first_with(data, "registration_questions")
    ti = info.get("ticket_info") if info else None
    qs = holder.get("registration_questions") if holder else None
    return (ti if isinstance(ti, dict) else None), (qs if isinstance(qs, list) else None)


def _lines(scan: dict) -> Iterator[str]:
    for line in (scan.get("text", "") or "").splitlines():
        line = line.strip().lstrip("✓✔•·-– ").strip()
        if 0 < len(line) <= 48:
            yield line


def going_status(scan: dict) -> Optional[str]:
    """The status line if the page says we're registered, e.g. "You're going"."""
    return next((line for line in _lines(scan) if GOING_LINE.match(line)), None)


def check_event(scan: dict, event: Event) -> EventCheck:
    """Step 1: free, spots left (or a waitlist), no host questions.

    Order of trust: the catalog's event fields, Luma's own page data, schema.org offers, then
    button labels and short status lines on the page."""
    if not event.free:
        return EventCheck(False, STOP_PAID)
    if event.has_questions:
        return EventCheck(False, STOP_QUESTIONS)

    status = going_status(scan)
    if status:
        return EventCheck(True, already_going=status)

    if any(CAPTCHA_FRAME.search(src) for src in scan.get("frames", [])) or \
            CAPTCHA_TEXT.search(scan.get("text", "")):
        return EventCheck(False, STOP_CAPTCHA)

    buttons = [" ".join(b.split()) for b in scan.get("buttons", [])]
    info, questions_data = _luma_data(scan.get("next"))
    ld = _ld_blocker(scan.get("ld", []))

    if info is not None:
        paid = info.get("is_free") is False
        full = info.get("is_sold_out") is True or info.get("spots_remaining") == 0
    else:
        paid = ld == STOP_PAID or any(PAID_BUTTON.match(b) for b in buttons)
        full = ld == STOP_FULL or any(FULL_LINE.match(line) for line in _lines(scan))
    full = full or event.spots_left == 0
    if paid:
        return EventCheck(False, STOP_PAID)
    if questions_data:
        return EventCheck(False, STOP_QUESTIONS)

    # A login-code prompt (step 2 handles it) hides the form, so skip the field checks then.
    asking_code = code_prompt(scan)
    asked = [] if asking_code else [
        f for f in scan.get("fields", [])
        if not NOTE_FIELD.search(f.get("label", "") + " " + f.get("name", ""))
        and not IDENTITY_FIELD.search(f.get("label", "") + " " + f.get("name", ""))]
    if asked:
        return EventCheck(False, STOP_QUESTIONS)

    if full:
        if any(WAITLIST_BUTTON.search(b) for b in buttons):
            return EventCheck(True, waitlist=True)
        return EventCheck(False, STOP_FULL)

    if asking_code or any(REGISTER_BUTTON.search(b) for b in buttons):
        return EventCheck(True)
    return EventCheck(True, inconclusive=True)


def code_prompt(scan: dict) -> bool:
    """True when the page asks for a code the site emailed or texted the person."""
    return bool(CODE_TEXT.search(scan.get("text", "") or "")) and bool(scan.get("fields"))


def is_waitlist(status: Optional[str]) -> bool:
    return bool(status) and bool(re.search(r"wait ?list", status, re.I))


def signed_in(scan: dict) -> bool:
    """Step 2: False when Luma shows a sign-in wall or a Sign in button."""
    url = (scan.get("url") or "").lower()
    if "/signin" in url or "/login" in url:
        return False
    if scan.get("signin_links", 0) > 0:
        return False
    return not any(SIGNIN_BUTTON.search(b) for b in scan.get("buttons", []))


def has_note_field(scan: dict) -> bool:
    return any(f.get("tag") == "textarea" or NOTE_FIELD.search(f.get("label", "") + " " +
                                                                f.get("name", ""))
               for f in scan.get("fields", []))


def disclosure_on_page(scan: dict, disclosure: str) -> bool:
    """True when a field on the page holds the disclosure text."""
    want = " ".join(disclosure.split()).lower()
    values = [f.get("value", "") for f in scan.get("fields", [])] + scan.get("editable", [])
    return any(want in " ".join(v.split()).lower() for v in values)
