"""Part 3 (Identity): PersonaIdentity against a mocked Persona API, plus FakeIdentity."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import contracts as c
from app.sponsors.identity import webhook
from app.sponsors.identity.fake import FakeIdentity
from app.sponsors.identity.persona_identity import PersonaError, PersonaIdentity

KEY = "persona_sandbox_test"
TEMPLATE = "itmpl_test123"
SECRET = "wbhsec_test"
REDIRECT = "http://localhost:5173/?verified=1"


class FakePersona:
    """Minimal in-memory Persona API behind httpx.MockTransport."""

    def __init__(self) -> None:
        self.inquiries: dict[str, dict] = {}
        self.calls: list[httpx.Request] = []
        self.reject_fields = False
        self.fail_get = False

    def add(self, inq_id: str, status: str, reference_id: str | None) -> None:
        self.inquiries[inq_id] = {"id": inq_id, "type": "inquiry",
                                  "attributes": {"status": status, "reference-id": reference_id}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v1/inquiries":
            attrs = json.loads(request.content)["data"]["attributes"]
            if self.reject_fields and "fields" in attrs:
                return httpx.Response(422, json={"errors": [{"title": "Invalid field", "details": "x"}]})
            inq_id = f"inq_{len(self.inquiries) + 1}"
            self.add(inq_id, "created", attrs.get("reference-id"))
            return httpx.Response(201, json={"data": self.inquiries[inq_id], "meta": {}})
        if request.method == "POST" and path.endswith("/resume"):
            inq_id = path.split("/")[-2]
            self.inquiries[inq_id]["attributes"]["status"] = "pending"
            return httpx.Response(200, json={"data": self.inquiries[inq_id],
                                             "meta": {"session-token": "sess_abc"}})
        if request.method == "GET" and path == "/api/v1/inquiries":
            ref = request.url.params.get("filter[reference-id]")
            statuses = request.url.params.get("filter[status]")
            allowed = set(statuses.split(",")) if statuses else None
            data = [i for i in reversed(list(self.inquiries.values()))  # newest first
                    if i["attributes"]["reference-id"] == ref
                    and (allowed is None or i["attributes"]["status"] in allowed)]
            return httpx.Response(200, json={"data": data, "links": {"prev": None, "next": None}})
        if request.method == "GET" and path.startswith("/api/v1/inquiries/"):
            if self.fail_get:
                return httpx.Response(500, json={})
            inq = self.inquiries.get(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"data": inq}) if inq else httpx.Response(404, json={})
        return httpx.Response(404, json={})

    def created(self) -> list[httpx.Request]:
        return [r for r in self.calls if r.method == "POST" and r.url.path == "/api/v1/inquiries"]


@pytest.fixture
def persona():
    return FakePersona()


def make(persona: FakePersona, secret: str = "") -> PersonaIdentity:
    return PersonaIdentity(api_key=KEY, template_id=TEMPLATE, webhook_secret=secret,
                           redirect_uri=REDIRECT, transport=httpx.MockTransport(persona.handler))


def user(**kw) -> c.User:
    return c.User(id=kw.pop("id", "demo"), name=kw.pop("name", "Maya Rao"), **kw)


def event(name: str, status: str | None, reference_id: str | None = "demo", inq: str = "inq_1") -> dict:
    attrs = {"reference-id": reference_id, "name-first": "Maya"}
    if status is not None:
        attrs["status"] = status
    return {"data": {"type": "event", "id": "evt_1", "attributes": {
        "name": name, "payload": {"data": {"type": "inquiry", "id": inq, "attributes": attrs}}}}}


# ---------------------------------------------------------------- interface


def test_implements_identity(persona):
    assert isinstance(make(persona), c.Identity)
    assert isinstance(FakeIdentity(), c.Identity)


def test_needs_keys(monkeypatch):
    monkeypatch.delenv("PERSONA_API_KEY", raising=False)
    monkeypatch.delenv("PERSONA_INQUIRY_TEMPLATE_ID", raising=False)
    with pytest.raises(RuntimeError, match="PERSONA_API_KEY"):
        PersonaIdentity()


def test_disclosure(persona):
    assert make(persona).disclosure(user(name="Maya R.")) == (
        "Registered by Event Agent for Maya R., a verified person. Reply to reach them directly.")


# ---------------------------------------------------------------- start


def test_start_creates_inquiry_with_reference_and_prefill(persona):
    session = make(persona).start(user())
    assert session.inquiry_id == "inq_1"
    (create,) = persona.created()
    assert create.headers["Authorization"] == f"Bearer {KEY}"
    assert create.headers["Persona-Version"] == "2023-01-05"
    attrs = json.loads(create.content)["data"]["attributes"]
    assert attrs == {"inquiry-template-id": TEMPLATE, "reference-id": "demo", "redirect-uri": REDIRECT,
                     "fields": {"name-first": "Maya", "name-last": "Rao"}}
    url = urlparse(session.url)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://inquiry.withpersona.com/verify"
    q = parse_qs(url.query)
    assert q["inquiry-id"] == ["inq_1"] and q["redirect-uri"] == [REDIRECT]
    assert "session-token" not in q and session.session_token is None  # created: link is enough
    assert "%26" not in session.url and "verified%3D1" in session.url  # redirect is escaped


def test_start_twice_reuses_the_same_inquiry(persona):
    identity = make(persona)
    first = identity.start(user())
    second = identity.start(user())
    assert first.inquiry_id == second.inquiry_id
    assert len(persona.created()) == 1


def test_start_reuses_pending_inquiry_with_session_token(persona):
    persona.add("inq_old", "pending", "demo")
    session = make(persona).start(user(persona_inquiry_id="inq_old"))
    assert session.inquiry_id == "inq_old"
    assert session.session_token == "sess_abc"
    assert parse_qs(urlparse(session.url).query)["session-token"] == ["sess_abc"]
    assert persona.created() == []


def test_start_finds_inquiry_by_reference_id(persona):
    persona.add("inq_other", "created", "someone-else")
    persona.add("inq_mine", "created", "demo")
    session = make(persona).start(user())  # fresh process: no stored inquiry id
    assert session.inquiry_id == "inq_mine"
    assert persona.created() == []
    listed = [r for r in persona.calls if r.method == "GET" and r.url.path == "/api/v1/inquiries"]
    assert listed[0].url.params["filter[reference-id]"] == "demo"


def test_start_after_failure_creates_a_new_inquiry(persona):
    persona.add("inq_old", "failed", "demo")
    session = make(persona).start(user(persona_inquiry_id="inq_old"))
    assert session.inquiry_id != "inq_old"
    assert len(persona.created()) == 1


def test_start_when_already_passed_opens_nothing(persona):
    persona.add("inq_done", "approved", "demo")
    session = make(persona).start(user())
    assert session.inquiry_id == "inq_done" and session.url is None
    assert persona.created() == []


def test_start_retries_without_name_prefill_if_template_rejects_it(persona):
    persona.reject_fields = True
    session = make(persona).start(user())
    assert session.inquiry_id == "inq_1"
    assert [("fields" in json.loads(r.content)["data"]["attributes"]) for r in persona.created()] == [True, False]


def test_start_raises_on_api_error_without_leaking_details():
    transport = httpx.MockTransport(lambda r: httpx.Response(
        401, json={"errors": [{"title": "Unauthorized", "details": "secret detail"}]}))
    identity = PersonaIdentity(api_key=KEY, template_id=TEMPLATE, transport=transport)
    with pytest.raises(PersonaError) as err:
        identity.start(user())
    assert "Unauthorized" in str(err.value) and "secret detail" not in str(err.value)


# ---------------------------------------------------------------- status


@pytest.mark.parametrize("status,passed", [("completed", True), ("approved", True), ("pending", False),
                                           ("created", False), ("failed", False), ("declined", False),
                                           ("needs_review", False), ("expired", False)])
def test_status_by_inquiry_id(persona, status, passed):
    persona.add("inq_x", status, "demo")
    assert make(persona).status(user(persona_inquiry_id="inq_x")) is passed


def test_status_after_start_then_completion(persona):
    identity = make(persona)
    session = identity.start(user())
    assert identity.status(user()) is False
    persona.inquiries[session.inquiry_id]["attributes"]["status"] = "completed"
    assert identity.status(user()) is True


def test_status_by_reference_id_lookup(persona):
    persona.add("inq_a", "failed", "demo")
    persona.add("inq_b", "completed", "demo")
    assert make(persona).status(user()) is True
    assert make(persona).status(user(id="nobody")) is False


def test_status_is_false_when_persona_errors(persona):
    persona.add("inq_x", "completed", "demo")
    persona.fail_get = True
    assert make(persona).status(user(persona_inquiry_id="inq_x")) is False


def test_status_true_for_already_verified_user_without_calls(persona):
    assert make(persona).status(user(verified=True)) is True
    assert persona.calls == []


# ---------------------------------------------------------------- webhook


@pytest.mark.parametrize("name,status,expected", [
    ("inquiry.completed", "completed", ("demo", True)),
    ("inquiry.approved", "approved", ("demo", True)),
    ("inquiry.failed", "failed", ("demo", False)),
    ("inquiry.declined", "declined", ("demo", False)),
    ("inquiry.created", "created", None),
    ("inquiry.started", "pending", None),
    ("inquiry.expired", "expired", None),
    ("inquiry.completed", None, ("demo", True)),  # no status: fall back to the event name
])
def test_webhook_events(persona, name, status, expected):
    assert make(persona).on_webhook(event(name, status)) == expected


def test_webhook_ignores_other_and_malformed_events(persona):
    identity = make(persona)
    assert identity.on_webhook(event("verification.passed", "passed")) is None
    assert identity.on_webhook({}) is None
    assert identity.on_webhook({"data": "nope"}) is None
    assert identity.on_webhook({"data": {"attributes": {"name": "inquiry.completed"}}}) is None


def test_webhook_maps_inquiry_to_user_when_reference_missing(persona):
    identity = make(persona)
    session = identity.start(user(id="u42"))
    assert identity.on_webhook(event("inquiry.completed", "completed", None, session.inquiry_id)) == ("u42", True)
    assert identity.on_webhook(event("inquiry.completed", "completed", None, "inq_unknown")) is None


def test_webhook_snake_case_keys(persona):
    body = event("inquiry.approved", "approved")
    attrs = body["data"]["attributes"]["payload"]["data"]["attributes"]
    attrs["reference_id"] = attrs.pop("reference-id")
    assert make(persona).on_webhook(body) == ("demo", True)


def signed(body: dict, secret: str = SECRET, t: str = "1763660000") -> tuple[str, str]:
    raw = json.dumps(body, separators=(",", ":"))
    return raw, f"t={t},v1={webhook.sign(secret, t, raw)}"


def test_webhook_signature_valid_via_envelope_and_kwargs(persona):
    identity = make(persona, secret=SECRET)
    body = event("inquiry.completed", "completed")
    raw, header = signed(body)
    assert identity.on_webhook({"raw_body": raw, "signature": header}) == ("demo", True)
    assert identity.on_webhook({"raw_body": raw.encode(), "headers": {"persona-signature": header}}) == ("demo", True)
    assert identity.on_webhook(body, raw_body=raw, signature=header) == ("demo", True)


def test_webhook_signature_rejections(persona):
    identity = make(persona, secret=SECRET)
    body = event("inquiry.completed", "completed")
    raw, header = signed(body)
    assert identity.on_webhook(body) is None  # secret set but nothing to check
    assert identity.on_webhook({"raw_body": raw, "signature": signed(body, secret="wrong")[1]}) is None
    tampered = raw.replace('"demo"', '"attacker"')
    assert identity.on_webhook({"raw_body": tampered, "signature": header}) is None
    assert identity.on_webhook({"raw_body": raw}) is None


def test_webhook_signature_rotation_any_set_matches(persona):
    identity = make(persona, secret=SECRET)
    raw, good = signed(event("inquiry.approved", "approved"))
    _, old = signed(event("inquiry.approved", "approved"), secret="wbhsec_old")
    assert identity.on_webhook({"raw_body": raw, "signature": f"{old} {good}"}) == ("demo", True)


def test_verify_signature_matches_persona_docs_scheme():
    import hashlib
    import hmac
    raw, t = '{"data":{}}', "1700000000"
    digest = hmac.new(SECRET.encode(), (t + "." + raw).encode(), "sha256").hexdigest()
    assert webhook.verify_signature(SECRET, raw, f"t={t},v1={digest}")
    assert not webhook.verify_signature(SECRET, raw, f"t={t},v1={hashlib.sha256(b'x').hexdigest()}")
    assert not webhook.verify_signature("", raw, f"t={t},v1={digest}")


# ---------------------------------------------------------------- fake


def test_fake_identity_still_works():
    fake = FakeIdentity()
    u = user()
    assert fake.status(u) is False
    assert fake.start(u).inquiry_id.startswith("inq_fake_")
    assert fake.status(u) is True
    assert fake.on_webhook({"user_id": "demo"}) == ("demo", True)
    assert fake.on_webhook(event("inquiry.completed", "completed")) == ("demo", True)
    assert fake.on_webhook({"raw_body": json.dumps({"user_id": "demo"}), "signature": "t=1,v1=x"}) == ("demo", True)
    assert fake.on_webhook({}) is None
    assert fake.disclosure(u) == c.DISCLOSURE_TEMPLATE.format(name="Maya Rao")


# ---------------------------------------------------------------- verified_at (for User.verified_at)


def test_verified_at_from_status_completed_and_approved(persona):
    from datetime import datetime, timezone
    persona.add("inq_c", "completed", "demo")
    persona.inquiries["inq_c"]["attributes"]["completed-at"] = "2026-11-20T21:05:00.000Z"
    identity = make(persona)
    assert identity.verified_at(user(persona_inquiry_id="inq_c")) is None  # nothing seen yet
    assert identity.status(user(persona_inquiry_id="inq_c")) is True
    assert identity.verified_at(user()) == datetime(2026, 11, 20, 21, 5, tzinfo=timezone.utc)

    persona.add("inq_a", "approved", "u2")
    persona.inquiries["inq_a"]["attributes"].update(
        {"completed-at": "2026-11-20T21:05:00Z", "decisioned-at": "2026-11-20T21:06:00Z"})
    assert identity.status(user(id="u2")) is True
    assert identity.verified_at(user(id="u2")) == datetime(2026, 11, 20, 21, 6, tzinfo=timezone.utc)


def test_verified_at_from_webhook(persona):
    from datetime import datetime, timezone
    body = event("inquiry.completed", "completed")
    body["data"]["attributes"]["payload"]["data"]["attributes"]["completed-at"] = "2026-11-20T21:05:00.000Z"
    identity = make(persona)
    assert identity.on_webhook(body) == ("demo", True)
    assert identity.verified_at(user()) == datetime(2026, 11, 20, 21, 5, tzinfo=timezone.utc)


def test_fake_verified_at():
    fake = FakeIdentity()
    assert fake.verified_at(user()) is None
    fake.start(user())
    assert fake.verified_at(user()) is not None
