"""Part 2: SieRanking. TF-IDF runs for real; SIE is mocked at the HTTP layer."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import contracts as c
from app.catalog.fake import FakeCatalog
from app.sponsors.ranking.sie_ranking import SieClient, SieNotConfigured, SieRanking

# A tiny "embedding model": one dimension per concept, so bouldering lands near climbing.
CONCEPTS = {
    "climb": {"climb", "climbing", "boulder", "bouldering", "boulders", "rock"},
    "agent": {"agent", "agents", "ai", "llm", "evals"},
    "art": {"ceramics", "pottery", "art", "wheel", "clay"},
    "social": {"social", "meetup", "mixer", "networking"},
    "weekend": {"weekend", "afternoon", "afternoons"},
}


def concept_vector(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.lower())
    return [sum(w in vocab for w in words) + 0.01 for vocab in CONCEPTS.values()]


class FakeSie:
    """Serves POST /v1/encode/{model} and /v1/score/{model} like SIE's JSON API."""

    def __init__(self, fail_encode: bool = False, fail_score: bool = False, status: int = 503) -> None:
        self.requests: list[tuple[str, dict, httpx.Headers]] = []
        self.fail_encode, self.fail_score, self.status = fail_encode, fail_score, status

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, body, request.headers))
        if path.startswith("/v1/encode/"):
            if self.fail_encode:
                return httpx.Response(self.status, text="unavailable")
            items = [{"id": item["id"], "dense": {"dims": len(CONCEPTS), "dtype": "float32",
                                                  "values": concept_vector(item["text"])}}
                     for item in body["items"]]
            return httpx.Response(200, json={"model": path[len("/v1/encode/"):], "items": items})
        if path.startswith("/v1/score/"):
            if self.fail_score:
                return httpx.Response(self.status, text="unavailable")
            q = concept_vector(body["query"]["text"])
            scored = [(item["id"], sum(a * b for a, b in zip(q, concept_vector(item["text"]))))
                      for item in body["items"]]
            top = max((s for _, s in scored), default=1.0) or 1.0
            scored.sort(key=lambda pair: -pair[1])
            return httpx.Response(200, json={"model": path[len("/v1/score/"):], "scores": [
                {"item_id": item_id, "score": s / top, "rank": rank} for rank, (item_id, s) in enumerate(scored)]})
        return httpx.Response(404)

    def doc_encodes(self) -> int:
        return sum(len(b["items"]) for p, b, _ in self.requests
                   if p.startswith("/v1/encode/") and not b.get("params", {}).get("options", {}).get("is_query"))

    def client(self, **kw) -> SieClient:
        return SieClient(api_key="sk-sie-test", base_url=None, embed_model="Qwen/Qwen3-Embedding-4B",
                         rerank_model="Qwen/Qwen3-Reranker-4B", max_retries=0,
                         transport=httpx.MockTransport(self.handler), **kw)


def _event(event_id: str, title: str, description: str = "", tags=(), days: int = 3) -> c.Event:
    return c.Event(id=event_id, title=title, description=description, tags=list(tags), url="https://lu.ma/x",
                   starts_at=datetime.now(timezone.utc) + timedelta(days=days))


SEMANTIC = [
    _event("boulder", "Dogpatch Boulders social night", "Bouldering for all levels, then drinks."),
    _event("agents", "Agent builders night", "Hands-on night for people building AI agents.", ["AI agents"]),
    _event("ceramics", "Ceramics for beginners", "A weekend afternoon at the wheel.", ["art"]),
]


def no_sie() -> SieRanking:
    return SieRanking(client=SieClient(api_key="", base_url=""), cache_path="")


# ---------------------------------------------------------------- TF-IDF arm (real)


def test_sie_ranking_matches_the_interface():
    assert isinstance(no_sie(), c.Ranking)


def test_tfidf_ranks_for_real():
    events = FakeCatalog().events()
    ranking = no_sie()
    ranking.encode_catalog(events)
    climbing = ranking.score("climbing", events, "tfidf")
    assert climbing[0][0] == "fake-bouldering"
    agents = dict(ranking.score("AI agents", events, "tfidf"))
    assert agents["fake-agent-builders"] > agents["fake-ceramics"]
    assert agents["fake-evals-workshop"] > agents["fake-ceramics"]
    assert {i for i, _ in climbing} == {e.id for e in events}
    assert [s for _, s in climbing] == sorted((s for _, s in climbing), reverse=True)


def test_tfidf_handles_plurals_and_unseen_candidates():
    ranking = no_sie()  # no encode_catalog: score() fits on the candidates it gets
    scores = dict(ranking.score("agent hackathons", SEMANTIC, "tfidf"))
    assert scores["agents"] > 0
    assert scores["ceramics"] == 0
    assert ranking.score("", SEMANTIC, "tfidf") == [(e.id, 0.0) for e in SEMANTIC]
    assert ranking.score("climbing", [], "tfidf") == []


# ---------------------------------------------------------------- SIE arm (mocked HTTP)


def test_sie_encodes_and_reranks_over_rest():
    sie = FakeSie()
    ranking = SieRanking(client=sie.client(), cache_path="")
    ranking.encode_catalog(SEMANTIC)
    result = ranking.score("rock climbing", SEMANTIC, "sie")
    assert result[0][0] == "boulder"  # semantic: "Bouldering" with no word "climbing"
    assert dict(ranking.score("rock climbing", SEMANTIC, "tfidf"))["boulder"] == 0  # TF-IDF can't see it
    assert ranking.last_fallback is None

    paths = [p for p, _, _ in sie.requests]
    assert "/v1/encode/Qwen/Qwen3-Embedding-4B" in paths
    assert "/v1/score/Qwen/Qwen3-Reranker-4B" in paths
    for _, _, headers in sie.requests:
        assert headers["authorization"] == "Bearer sk-sie-test"
        assert headers["accept"] == "application/json"
    query = next(b for p, b, _ in sie.requests if p.startswith("/v1/encode/") and b["params"].get("options"))
    assert query["params"]["options"] == {"is_query": True}
    assert query["params"]["output_types"] == ["dense"]
    assert query["items"] == [{"id": "0", "text": "rock climbing"}]
    score_body = next(b for p, b, _ in sie.requests if p.startswith("/v1/score/"))
    assert score_body["query"] == {"text": "rock climbing"}
    assert {i["id"] for i in score_body["items"]} == {"boulder", "agents", "ceramics"}


def test_sie_encodes_catalog_once_and_caches_results():
    sie = FakeSie()
    ranking = SieRanking(client=sie.client(), cache_path="")
    ranking.encode_catalog(SEMANTIC)
    ranking.encode_catalog(SEMANTIC)
    assert sie.doc_encodes() == len(SEMANTIC)
    ranking.score("AI agents", SEMANTIC, "sie")
    before = len(sie.requests)
    assert ranking.score("AI agents", SEMANTIC, "sie")[0][0] == "agents"
    assert len(sie.requests) == before  # same taste and candidates: served from memory


def test_sie_reranks_only_the_top_40():
    events = [_event(f"e{i}", f"Event {i}", "AI agents" if i % 2 else "pottery") for i in range(60)]
    sie = FakeSie()
    ranking = SieRanking(client=sie.client(), cache_path="")
    result = ranking.score("AI agents", events, "sie")
    assert len(result) == 60
    score_body = next(b for p, b, _ in sie.requests if p.startswith("/v1/score/"))
    assert len(score_body["items"]) == 40


def test_sie_file_cache_survives_restart(tmp_path):
    path = tmp_path / "sie_cache.json"
    first = FakeSie()
    SieRanking(client=first.client(), cache_path=str(path)).encode_catalog(SEMANTIC)
    assert path.exists() and len(json.loads(path.read_text())["vectors"]) == len(SEMANTIC)

    second = FakeSie()
    ranking = SieRanking(client=second.client(), cache_path=str(path))
    ranking.encode_catalog(SEMANTIC)
    assert second.doc_encodes() == 0
    assert ranking.score("bouldering", SEMANTIC, "sie")[0][0] == "boulder"

    changed = [SEMANTIC[0].model_copy(update={"description": "New text"}), *SEMANTIC[1:]]
    ranking.encode_catalog(changed)
    assert second.doc_encodes() == 1  # content hash changed for one event


def test_sie_failure_falls_back_to_tfidf_and_cools_down():
    sie = FakeSie(fail_encode=True)
    ranking = SieRanking(client=sie.client(), cache_path="")
    ranking.encode_catalog(SEMANTIC)  # logs, doesn't raise
    got = ranking.score("AI agents", SEMANTIC, "sie")
    assert got == ranking.score("AI agents", SEMANTIC, "tfidf")
    assert ranking.last_fallback and "503" in ranking.last_fallback
    calls = len(sie.requests)
    ranking.score("climbing", SEMANTIC, "sie")
    assert len(sie.requests) == calls  # cooling down: no new SIE calls


def test_rerank_failure_keeps_sie_cosine():
    sie = FakeSie(fail_score=True)
    ranking = SieRanking(client=sie.client(), cache_path="")
    result = ranking.score("rock climbing", SEMANTIC, "sie")
    assert result[0][0] == "boulder"  # cosine over SIE embeddings, not TF-IDF
    assert ranking.last_fallback is None


def test_sie_without_key_raises_clearly_and_ranker_falls_back():
    client = SieClient(api_key="", base_url="")
    assert not client.configured
    with pytest.raises(SieNotConfigured, match="SIE_API_KEY"):
        client.encode(["hi"])
    ranking = SieRanking(client=client, cache_path="")
    assert ranking.score("climbing", SEMANTIC, "sie") == ranking.score("climbing", SEMANTIC, "tfidf")


def test_sie_client_reads_env(monkeypatch):
    monkeypatch.setenv("SIE_API_KEY", "sk-sie-env")
    monkeypatch.delenv("SIE_BASE_URL", raising=False)
    client = SieClient()
    assert client.base_url == "https://api.superlinked.com"
    monkeypatch.setenv("SIE_BASE_URL", "http://localhost:8080/")
    assert SieClient().base_url == "http://localhost:8080"
    monkeypatch.delenv("SIE_API_KEY")
    assert SieClient().configured  # self-hosted SIE needs no key
