"""Persona identity verification (sandbox), over Persona's REST API with plain httpx.

Endpoints used (docs.withpersona.com/api-reference/inquiries):
  POST /api/v1/inquiries                     create an inquiry from a template
  GET  /api/v1/inquiries/{inquiry-id}        read its status
  GET  /api/v1/inquiries?filter[...]         find the user's inquiries by reference id
  POST /api/v1/inquiries/{inquiry-id}/resume session token for a `pending` inquiry
Hosted flow (docs.withpersona.com/hosted-flow-parameters):
  https://inquiry.withpersona.com/verify?inquiry-id=...&session-token=...&redirect-uri=...

We keep only the inquiry id and pass/fail. ID images, selfies and document data stay with
Persona and are never read, stored or logged here.

Env: PERSONA_API_KEY (sandbox key), PERSONA_INQUIRY_TEMPLATE_ID (itmpl_...),
optional PERSONA_WEBHOOK_SECRET (wbhsec_...), PERSONA_REDIRECT_URI, PERSONA_API_VERSION.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.contracts import DISCLOSURE_TEMPLATE, User, VerifySession
from app.sponsors.identity import webhook

log = logging.getLogger(__name__)

API_BASE = "https://api.withpersona.com/api/v1"
HOSTED_FLOW_URL = "https://inquiry.withpersona.com/verify"
# Pinned so response shapes don't change with the key's dashboard default. 2023-01-05 is the
# version Persona's own API quickstart uses; it accepts every field sent below.
DEFAULT_API_VERSION = "2023-01-05"
DEFAULT_REDIRECT_URI = "http://localhost:5173/?verified=1"

# A `created` inquiry opens for anyone with the link; a `pending` one needs a session token
# from /resume. Both are reused instead of creating a duplicate.
REUSABLE_STATUSES = ("created", "pending")


class PersonaError(RuntimeError):
    """Persona API call failed. The message never includes payload data."""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PersonaIdentity:
    def __init__(self, api_key: Optional[str] = None, template_id: Optional[str] = None,
                 webhook_secret: Optional[str] = None, redirect_uri: Optional[str] = None,
                 api_version: Optional[str] = None,
                 transport: Optional[httpx.BaseTransport] = None) -> None:
        self.api_key = api_key or os.getenv("PERSONA_API_KEY", "").strip()
        self.template_id = template_id or os.getenv("PERSONA_INQUIRY_TEMPLATE_ID", "").strip()
        self.webhook_secret = (webhook_secret if webhook_secret is not None
                               else os.getenv("PERSONA_WEBHOOK_SECRET", "").strip())
        self.redirect_uri = (redirect_uri if redirect_uri is not None
                             else os.getenv("PERSONA_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip())
        if not self.api_key or not self.template_id:
            raise RuntimeError(
                "PersonaIdentity needs PERSONA_API_KEY and PERSONA_INQUIRY_TEMPLATE_ID. "
                "Set them in backend/.env, or keep identity on its fake (SPONSORS_FAKE).")
        if not self.api_key.startswith("persona_sandbox_"):
            log.warning("PERSONA_API_KEY does not look like a sandbox key")
        self._http = httpx.Client(
            base_url=API_BASE,
            transport=transport,
            timeout=15.0,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Persona-Version": api_version or os.getenv("PERSONA_API_VERSION", DEFAULT_API_VERSION),
                "Key-Inflection": "kebab",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._inquiry_by_user: dict[str, str] = {}
        self._user_by_inquiry: dict[str, str] = {}
        self._passed_at: dict[str, datetime] = {}

    # ------------------------------------------------------------ Identity

    def start(self, user: User) -> VerifySession:
        inquiry = self._existing_inquiry(user, statuses=REUSABLE_STATUSES + tuple(webhook.PASSED_STATUSES))
        token: Optional[str] = None
        if inquiry is not None:
            status = _status(inquiry)
            if status in webhook.PASSED_STATUSES:
                self._remember(user.id, inquiry["id"])
                return VerifySession(inquiry_id=inquiry["id"])  # already verified, nothing to open
            if status == "pending":
                token = self._resume(inquiry["id"])
            log.info("persona: reusing inquiry %s (%s)", inquiry["id"], status)
        else:
            inquiry, token = self._create(user)
            log.info("persona: created inquiry %s", inquiry["id"])
        self._remember(user.id, inquiry["id"])
        return VerifySession(inquiry_id=inquiry["id"], session_token=token,
                             url=self.hosted_url(inquiry["id"], token))

    def status(self, user: User) -> bool:
        if user.verified:
            return True
        try:
            inquiry_id = user.persona_inquiry_id or self._inquiry_by_user.get(user.id)
            inquiries = [self._get(inquiry_id)] if inquiry_id else self._list(user.id)
            passed = next((i for i in inquiries if _status(i) in webhook.PASSED_STATUSES), None)
            if passed is None:
                return False
            when = webhook.passed_at(passed.get("attributes") or {})
            if when:
                self._passed_at[user.id] = when
            return True
        except PersonaError as exc:
            log.warning("persona: status check failed for user %s: %s", user.id, exc)
            return False

    def on_webhook(self, payload: dict, raw_body: Optional[webhook.Body] = None,
                   signature: Optional[str] = None) -> Optional[tuple[str, bool]]:
        """Pass the parsed body, or (needed when PERSONA_WEBHOOK_SECRET is set) the raw body
        and the Persona-Signature header: either as keyword args or as the envelope
        {"raw_body": ..., "signature": ...}. Returns (user_id, verified) for decisive
        inquiry events (completed/approved -> True, failed/declined -> False), else None."""
        event, raw_body, signature = webhook.unwrap(payload, raw_body, signature)
        if self.webhook_secret and not webhook.verify_signature(self.webhook_secret, raw_body, signature):
            log.warning("persona: webhook rejected, bad or missing %s", webhook.SIGNATURE_HEADER)
            return None
        parsed = webhook.parse_inquiry_event(event)
        if parsed is None:
            return None
        name, inquiry_id, status, reference_id, when = parsed
        user_id = reference_id or self._user_by_inquiry.get(inquiry_id)
        verified = webhook.verdict(name, status)
        log.info("persona: webhook %s for inquiry %s (%s)", name, inquiry_id, status)
        if not user_id or verified is None:
            return None
        self._remember(user_id, inquiry_id)
        if verified and when:
            self._passed_at[user_id] = when
        return user_id, verified

    def disclosure(self, user: User) -> str:
        return DISCLOSURE_TEMPLATE.format(name=user.name)

    def verified_at(self, user: User) -> Optional[datetime]:
        """Not part of Identity: when Persona says the person passed (inquiry `completed-at`,
        or `decisioned-at` if approved), as seen by the last status() or webhook. For User.verified_at."""
        return self._passed_at.get(user.id)

    # ------------------------------------------------------------ helpers

    def hosted_url(self, inquiry_id: str, session_token: Optional[str] = None) -> str:
        params = {"inquiry-id": inquiry_id}
        if session_token:
            params["session-token"] = session_token
        if self.redirect_uri:
            params["redirect-uri"] = self.redirect_uri
        return f"{HOSTED_FLOW_URL}?{urlencode(params)}"

    def _existing_inquiry(self, user: User, statuses: tuple[str, ...]) -> Optional[dict]:
        known = user.persona_inquiry_id or self._inquiry_by_user.get(user.id)
        if known:
            try:
                inquiry = self._get(known)
                if _status(inquiry) in statuses:
                    return inquiry
            except PersonaError as exc:
                log.warning("persona: could not read inquiry %s: %s", known, exc)
        found = [i for i in self._list(user.id, statuses) if _status(i) in statuses]  # newest first
        passed = [i for i in found if _status(i) in webhook.PASSED_STATUSES]
        return (passed or found or [None])[0]

    def _create(self, user: User) -> tuple[dict, Optional[str]]:
        attributes: dict = {_template_key(self.template_id): self.template_id, "reference-id": user.id}
        if self.redirect_uri:
            attributes["redirect-uri"] = self.redirect_uri
        fields = _name_fields(user.name)
        try:
            body = self._request("POST", "/inquiries",
                                 json={"data": {"attributes": {**attributes, "fields": fields}}}
                                 if fields else {"data": {"attributes": attributes}})
        except PersonaError as exc:
            if not fields or exc.status_code not in (400, 422):
                raise
            # The template may not define name fields; prefilling is optional.
            log.info("persona: create with name prefill rejected (%s), retrying without", exc.status_code)
            body = self._request("POST", "/inquiries", json={"data": {"attributes": attributes}})
        meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
        return _inquiry(body), meta.get("session-token")

    def _get(self, inquiry_id: str) -> dict:
        return _inquiry(self._request("GET", f"/inquiries/{inquiry_id}"))

    def _list(self, user_id: str, statuses: tuple[str, ...] = ()) -> list[dict]:
        params = {"filter[reference-id]": user_id, "page[size]": "10"}
        if statuses:
            params["filter[status]"] = ",".join(statuses)
        data = self._request("GET", "/inquiries", params=params).get("data")
        return [i for i in data if isinstance(i, dict) and i.get("id")] if isinstance(data, list) else []

    def _resume(self, inquiry_id: str) -> Optional[str]:
        meta = self._request("POST", f"/inquiries/{inquiry_id}/resume").get("meta") or {}
        return meta.get("session-token")

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            resp = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise PersonaError(f"{method} {path}: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise PersonaError(f"{method} {path}: HTTP {resp.status_code} {_error_titles(resp)}".strip(),
                               resp.status_code)
        try:
            body = resp.json()
        except ValueError as exc:
            raise PersonaError(f"{method} {path}: response was not JSON") from exc
        return body if isinstance(body, dict) else {}

    def _remember(self, user_id: str, inquiry_id: str) -> None:
        self._inquiry_by_user[user_id] = inquiry_id
        self._user_by_inquiry[inquiry_id] = user_id


def _inquiry(body: dict) -> dict:
    data = body.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        raise PersonaError("response had no inquiry")
    return data


def _status(inquiry: dict) -> Optional[str]:
    attrs = inquiry.get("attributes")
    return webhook.attr(attrs, "status") if isinstance(attrs, dict) else None


def _template_key(template_id: str) -> str:
    # Create-an-inquiry takes exactly one of these, chosen by the id's prefix.
    if template_id.startswith("itmplv_"):
        return "inquiry-template-version-id"
    if template_id.startswith("tmpl_"):
        return "template-id"
    return "inquiry-template-id"


def _name_fields(name: str) -> dict:
    parts = (name or "").split()
    if not parts:
        return {}
    fields = {"name-first": parts[0]}
    if len(parts) > 1:
        fields["name-last"] = " ".join(parts[1:])
    return fields


def _error_titles(resp: httpx.Response) -> str:
    # Titles only: `details` can echo submitted values.
    try:
        errors = resp.json().get("errors") or []
        return "; ".join(str(e.get("title")) for e in errors if isinstance(e, dict) and e.get("title"))
    except (ValueError, AttributeError):
        return ""
