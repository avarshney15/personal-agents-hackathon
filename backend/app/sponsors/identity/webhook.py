"""Persona webhook helpers (stdlib only, shared by PersonaIdentity and FakeIdentity).

Signature scheme (docs.withpersona.com/webhooks-best-practices): the `Persona-Signature`
header holds one or more space-separated sets `t=<unix_ts>,v1=<hex>`; each v1 is
HMAC-SHA256(secret, f"{t}.{raw_body}") as hex. Accept if any v1 matches. Always use the
raw request body, never re-serialized JSON.

Event body (docs.withpersona.com/api-reference/webhooks/inquiry-events/...):
  {"data": {"type": "event", "id": "evt_...",
            "attributes": {"name": "inquiry.completed",
                           "payload": {"data": {"id": "inq_...",
                                                "attributes": {"status": "completed",
                                                               "reference-id": "<user id>"}}}}}}

Only the event name, inquiry id, status and reference id are read. Nothing else in the
payload (which can include document data) is kept or logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping, Optional, Union

SIGNATURE_HEADER = "Persona-Signature"

# Inquiry statuses that mean the person passed (docs: model-lifecycle). `completed` is set
# when every required verification passed; `approved` is the post-inquiry decision.
PASSED_STATUSES = frozenset({"completed", "approved"})
# Final "did not pass" outcomes. Other events (created, started, expired, marked-for-review,
# transitioned, ...) are not decisive and are ignored so they can't un-verify someone.
FAILED_STATUSES = frozenset({"failed", "declined"})
PASSED_EVENTS = frozenset({"inquiry.completed", "inquiry.approved"})
FAILED_EVENTS = frozenset({"inquiry.failed", "inquiry.declined"})

Body = Union[bytes, str]


def sign(secret: str, timestamp: str, raw_body: Body) -> str:
    """The hex HMAC Persona puts in v1= (used by tests and by verify_signature)."""
    body = raw_body.decode() if isinstance(raw_body, bytes) else raw_body
    return hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def verify_signature(secret: str, raw_body: Optional[Body], header: Optional[str]) -> bool:
    if not secret or raw_body is None or not header:
        return False
    for sig_set in header.split(" "):
        parts = dict(p.split("=", 1) for p in sig_set.split(",") if "=" in p)
        t, v1 = parts.get("t"), parts.get("v1")
        if t and v1 and hmac.compare_digest(v1, sign(secret, t, raw_body)):
            return True
    return False


def unwrap(payload: Any, raw_body: Optional[Body] = None,
           signature: Optional[str] = None) -> tuple[Optional[dict], Optional[Body], Optional[str]]:
    """Accept either the parsed event dict, or an envelope from the route:
    {"raw_body": <str|bytes>, "signature": <Persona-Signature value>}  (or "headers": {...}).
    Returns (event dict, raw body, signature header)."""
    if isinstance(payload, Mapping) and "raw_body" in payload:
        raw_body = payload.get("raw_body")
        signature = payload.get("signature") or _header(payload.get("headers"), SIGNATURE_HEADER)
        try:
            event = json.loads(raw_body) if raw_body is not None else None
        except (TypeError, ValueError):
            event = None
        return (event if isinstance(event, dict) else None), raw_body, signature
    return (payload if isinstance(payload, dict) else None), raw_body, signature


def parse_inquiry_event(event: Optional[dict]) -> Optional[tuple[str, str, Optional[str], Optional[str]]]:
    """-> (event name, inquiry id, status, reference id) for inquiry.* events, else None."""
    if not isinstance(event, dict):
        return None
    data = event.get("data")
    attrs = data.get("attributes") if isinstance(data, dict) else None
    if not isinstance(attrs, dict):
        return None
    name = attrs.get("name")
    payload = attrs.get("payload")
    inquiry = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(name, str) or not name.startswith("inquiry.") or not isinstance(inquiry, dict):
        return None
    inquiry_id = inquiry.get("id")
    if not isinstance(inquiry_id, str):
        return None
    i_attrs = inquiry.get("attributes") if isinstance(inquiry.get("attributes"), dict) else {}
    return name, inquiry_id, attr(i_attrs, "status"), attr(i_attrs, "reference-id")


def verdict(name: str, status: Optional[str]) -> Optional[bool]:
    """True = passed, False = failed or declined, None = not decisive."""
    if status in PASSED_STATUSES or (status is None and name in PASSED_EVENTS):
        return True
    if status in FAILED_STATUSES or (status is None and name in FAILED_EVENTS):
        return False
    return None


def attr(attrs: Mapping, kebab_key: str) -> Optional[Any]:
    """Read an attribute whatever the Key-Inflection (kebab, snake or camel)."""
    head, *rest = kebab_key.split("-")
    for key in (kebab_key, kebab_key.replace("-", "_"), head + "".join(w.title() for w in rest)):
        if key in attrs:
            return attrs[key]
    return None


def _header(headers: Any, name: str) -> Optional[str]:
    if not isinstance(headers, Mapping):
        return None
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return value
    return None
