"""Superlinked SIE ranking (arm "sie") with TF-IDF as the control arm ("tfidf"). Owned by Part 2.

Arm "sie": encode() every event once with SIE, cosine-rank candidates against the encoded taste,
then score() (rerank) the top 40 with SIE's reranker. Arm "tfidf": scikit-learn TF-IDF cosine,
no key needed. If SIE fails or isn't configured, that call falls back to TF-IDF and logs why.

SIE is called over its REST API (https://superlinked.com/docs, "POST /v1/encode/{model}" and
"POST /v1/score/{model}") with httpx, because the official `sie-sdk` needs Python 3.12+ and this
backend runs on 3.11. The wire shapes match what `sie_sdk.SIEClient` sends.

Env:
  SIE_API_KEY       SIE Serverless key (sent as a Bearer token). Required for arm "sie".
  SIE_BASE_URL      Optional. Defaults to SIE Serverless, https://api.superlinked.com.
                    Set it (e.g. http://localhost:8080) to use a self-hosted SIE; no key needed then.
  SIE_EMBED_MODEL   Default Qwen/Qwen3-Embedding-4B.
  SIE_RERANK_MODEL  Default Qwen/Qwen3-Reranker-4B. Set it empty to skip the rerank step.
  SIE_CACHE_PATH    Vector cache file. Default <repo>/data/sie_cache.json. Set it empty to disable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

import numpy as np

from app.contracts import Arm, Event

log = logging.getLogger("ranking")

REPO_ROOT = Path(__file__).resolve().parents[4]
SIE_SERVERLESS_BASE_URL = "https://api.superlinked.com"
DEFAULT_EMBED_MODEL = "Qwen/Qwen3-Embedding-4B"
DEFAULT_RERANK_MODEL = "Qwen/Qwen3-Reranker-4B"
DEFAULT_INSTRUCTION = "Given what a person is into lately, find local events they would enjoy"

RERANK_TOP = 40  # canvas: "encode() the catalog once, score() the top 40"
ENCODE_BATCH = 32
COSINE_WEIGHT = 0.3  # final sie score = 0.3 * cosine + 0.7 * rerank (top 40 only)
SIE_COOLDOWN_S = 60.0  # after a failure, skip SIE for this long so the feed stays fast
RESULT_CACHE_SIZE = 256


class SieError(RuntimeError):
    """SIE call failed (network, HTTP error, or an unexpected response)."""


class SieNotConfigured(SieError):
    """No SIE key or base URL set."""


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


# ---------------------------------------------------------------- SIE REST client


class SieClient:
    """Thin client for SIE's encode and score endpoints (JSON wire format)."""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 embed_model: Optional[str] = None, rerank_model: Optional[str] = None,
                 timeout_s: float = 10.0, max_retries: int = 1, transport: Any = None) -> None:
        self.api_key = _env("SIE_API_KEY", "") if api_key is None else api_key.strip()
        explicit = _env("SIE_BASE_URL", "") if base_url is None else base_url.strip()
        self.base_url = (explicit or (SIE_SERVERLESS_BASE_URL if self.api_key else "")).rstrip("/")
        self.embed_model = embed_model or _env("SIE_EMBED_MODEL", DEFAULT_EMBED_MODEL) or DEFAULT_EMBED_MODEL
        self.rerank_model = _env("SIE_RERANK_MODEL", DEFAULT_RERANK_MODEL) if rerank_model is None else rerank_model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self._transport = transport
        self._http = None
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _client(self):
        if not self.configured:
            raise SieNotConfigured(
                "SIE is not configured: set SIE_API_KEY for SIE Serverless "
                f"({SIE_SERVERLESS_BASE_URL}), or SIE_BASE_URL for a self-hosted SIE."
            )
        with self._lock:
            if self._http is None:
                import httpx

                headers = {"Content-Type": "application/json", "Accept": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                self._http = httpx.Client(base_url=self.base_url, headers=headers,
                                          timeout=self.timeout_s, transport=self._transport)
            return self._http

    def _post(self, path: str, body: dict, timeout_s: Optional[float] = None) -> dict:
        import httpx

        http = self._client()
        last = ""
        for attempt in range(self.max_retries + 1):
            try:
                resp = http.post(path, json=body, timeout=timeout_s or self.timeout_s)
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
                continue
            if resp.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                last = f"HTTP {resp.status_code}"
                try:
                    wait = float(resp.headers.get("retry-after", "1"))
                except ValueError:
                    wait = 1.0
                time.sleep(min(max(wait, 0.0), 2.0))
                continue
            if resp.status_code >= 400:
                raise SieError(f"SIE {path} returned HTTP {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()
            except ValueError as exc:
                raise SieError(f"SIE {path} returned non-JSON: {resp.text[:200]}") from exc
        raise SieError(f"SIE {path} failed after {self.max_retries + 1} tries ({last})")

    def encode(self, texts: list[str], *, is_query: bool = False, instruction: Optional[str] = None,
               timeout_s: Optional[float] = None) -> list[np.ndarray]:
        """Dense vectors, one per text. Body: {"items": [...], "params": {...}}."""
        out: list[np.ndarray] = []
        for start in range(0, len(texts), ENCODE_BATCH):
            chunk = texts[start:start + ENCODE_BATCH]
            params: dict[str, Any] = {"output_types": ["dense"]}
            if is_query:
                params["options"] = {"is_query": True}
            if instruction:
                params["instruction"] = instruction
            body = {"items": [{"id": str(i), "text": t} for i, t in enumerate(chunk)], "params": params}
            data = self._post(f"/v1/encode/{self.embed_model}", body, timeout_s)
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list) or len(items) != len(chunk):
                raise SieError(f"SIE encode returned {len(items or [])} items for {len(chunk)} inputs")
            by_id = {str(item.get("id")): item for item in items if isinstance(item, dict)}
            for i, item in enumerate(items):
                item = by_id.get(str(i), item)
                dense = item.get("dense") if isinstance(item, dict) else None
                values = dense.get("values") if isinstance(dense, dict) else dense
                if not values:
                    raise SieError("SIE encode response has no dense vector")
                out.append(np.asarray(values, dtype=np.float32))
        return out

    def score(self, query: str, items: list[tuple[str, str]], *, instruction: Optional[str] = None,
              timeout_s: Optional[float] = None) -> dict[str, float]:
        """Rerank (id, text) pairs against the query. Returns {id: score}, higher is better."""
        if not self.rerank_model:
            raise SieError("No SIE rerank model set (SIE_RERANK_MODEL is empty)")
        body: dict[str, Any] = {"query": {"text": query},
                                "items": [{"id": item_id, "text": text} for item_id, text in items]}
        if instruction:
            body["instruction"] = instruction
        data = self._post(f"/v1/score/{self.rerank_model}", body, timeout_s)
        scores = data.get("scores") if isinstance(data, dict) else None
        if not isinstance(scores, list):
            raise SieError("SIE score response has no scores")
        return {str(s["item_id"]): float(s["score"]) for s in scores if s.get("item_id") is not None}


# ---------------------------------------------------------------- TF-IDF (control arm)

_TOKEN = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _analyze(text: str) -> list[str]:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

    words = [_stem(w) for w in _TOKEN.findall(text.lower()) if w not in ENGLISH_STOP_WORDS]
    return words + [f"{a} {b}" for a, b in zip(words, words[1:])]


def event_text(event: Event) -> str:
    """What gets encoded for an event: title, description, tags, host."""
    parts = [event.title.strip()]
    if event.description.strip():
        parts.append(event.description.strip()[:1500])
    if event.tags:
        parts.append("Tags: " + ", ".join(event.tags))
    if event.host.strip():
        parts.append("Host: " + event.host.strip())
    return ". ".join(p.rstrip(".") for p in parts) + "."


def _tfidf_doc(event: Event) -> str:
    # Title and tags count twice: they say what the event is about.
    tags = " ".join(event.tags)
    return " ".join([event.title, event.title, tags, tags, event.host, event.description])


class _Tfidf:
    def __init__(self) -> None:
        self.vectorizer = None
        self.matrix = None
        self.rows: dict[str, int] = {}
        self.sigs: dict[str, str] = {}

    def covers(self, events: list[Event]) -> bool:
        return all(self.sigs.get(e.id) == _sig(_tfidf_doc(e)) for e in events)

    def fit(self, events: list[Event]) -> None:
        from sklearn.feature_extraction.text import TfidfVectorizer

        docs = [_tfidf_doc(e) for e in events]
        self.rows = {e.id: i for i, e in enumerate(events)}
        self.sigs = {e.id: _sig(d) for e, d in zip(events, docs)}
        self.vectorizer = TfidfVectorizer(analyzer=_analyze, sublinear_tf=True)
        try:
            self.matrix = self.vectorizer.fit_transform(docs)
        except ValueError:  # empty vocabulary
            self.vectorizer, self.matrix = None, None

    def score(self, taste: str, events: list[Event]) -> dict[str, float]:
        if self.vectorizer is None or self.matrix is None or not taste.strip():
            return {e.id: 0.0 for e in events}
        query = self.vectorizer.transform([taste])
        rows = [self.rows[e.id] for e in events]
        sims = (self.matrix[rows] @ query.T).toarray().ravel()  # rows are L2-normalized
        return {e.id: float(s) for e, s in zip(events, sims)}


def _sig(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _sorted(scores: dict[str, float], candidates: list[Event]) -> list[tuple[str, float]]:
    # Stable: ties keep the candidates' order (soonest first from the catalog).
    pairs = [(e.id, float(scores.get(e.id, 0.0))) for e in candidates]
    return sorted(pairs, key=lambda pair: -pair[1])


def _to_unit(score: float) -> float:
    return score if 0.0 <= score <= 1.0 else 1.0 / (1.0 + math.exp(-score))


# ---------------------------------------------------------------- the ranker


class SieRanking:
    """Implements app.contracts.Ranking."""

    def __init__(self, client: Optional[SieClient] = None, cache_path: Optional[str] = None,
                 rerank_top: int = RERANK_TOP) -> None:
        self._client = client
        path = _env("SIE_CACHE_PATH", str(REPO_ROOT / "data" / "sie_cache.json")) if cache_path is None else cache_path
        self._cache_path = Path(path) if path else None
        self._rerank_top = rerank_top
        self._lock = threading.RLock()
        self._events: dict[str, Event] = {}
        self._tfidf = _Tfidf()
        self._vectors: dict[str, np.ndarray] = {}  # "<event id>:<content hash>" -> unit vector
        self._file_loaded = False
        self._results: OrderedDict[tuple, list[tuple[str, float]]] = OrderedDict()
        self._sie_down_until = 0.0
        self._instruction: Optional[str] = _env("SIE_INSTRUCTION", DEFAULT_INSTRUCTION) or None
        self._warned_unconfigured = False
        self.last_fallback: Optional[str] = None  # why the last "sie" call used TF-IDF, if it did

    # ------------------------------------------------ public interface

    def encode_catalog(self, events: list[Event]) -> None:
        with self._lock:
            for event in events:
                self._events[event.id] = event
            self._tfidf.fit(list(self._events.values()))
            self._results.clear()
        client = self.client
        if not client.configured:
            self._warn_unconfigured()
            return
        try:
            self._ensure_vectors(events, timeout_s=60.0)
        except SieError as exc:
            log.warning("SIE encode_catalog failed, arm sie will use TF-IDF until it works: %s", exc)

    def score(self, taste: str, candidates: list[Event], arm: Arm) -> list[tuple[str, float]]:
        if not candidates:
            return []
        if not taste.strip():
            return [(e.id, 0.0) for e in candidates]
        key = (arm, taste, tuple((e.id, _sig(_tfidf_doc(e))) for e in candidates))
        with self._lock:
            cached = self._results.get(key)
            if cached is not None:
                self._results.move_to_end(key)
                return list(cached)
        if arm == "sie":
            try:
                result = self._score_sie(taste, candidates)
                self.last_fallback = None
            except SieError as exc:
                self.last_fallback = str(exc)
                if isinstance(exc, SieNotConfigured):
                    self._warn_unconfigured()
                else:
                    log.warning("SIE scoring failed, using TF-IDF for this call: %s", exc)
                    self._sie_down_until = time.monotonic() + SIE_COOLDOWN_S
                return self._score_tfidf(taste, candidates)  # not cached, so SIE gets retried
        else:
            result = self._score_tfidf(taste, candidates)
        with self._lock:
            self._results[key] = result
            while len(self._results) > RESULT_CACHE_SIZE:
                self._results.popitem(last=False)
        return list(result)

    # ------------------------------------------------ arms

    @property
    def client(self) -> SieClient:
        if self._client is None:
            self._client = SieClient()
        return self._client

    def _score_tfidf(self, taste: str, candidates: list[Event]) -> list[tuple[str, float]]:
        with self._lock:
            if not self._tfidf.covers(candidates):
                for event in candidates:
                    self._events[event.id] = event
                self._tfidf.fit(list(self._events.values()))
            scores = self._tfidf.score(taste, candidates)
        return _sorted(scores, candidates)

    def _score_sie(self, taste: str, candidates: list[Event]) -> list[tuple[str, float]]:
        client = self.client
        if not client.configured:
            raise SieNotConfigured("SIE is not configured (set SIE_API_KEY or SIE_BASE_URL)")
        if time.monotonic() < self._sie_down_until:
            raise SieError("SIE failed recently; cooling down")
        vectors = self._ensure_vectors(candidates)
        query = self._encode_query(taste)
        cosine = {e.id: float(np.dot(query, vectors[e.id])) for e in candidates}
        shortlist = sorted(candidates, key=lambda e: -cosine[e.id])[: self._rerank_top]
        final = {e.id: COSINE_WEIGHT * max(cosine[e.id], 0.0) for e in candidates}
        if client.rerank_model and shortlist:
            try:
                reranked = self._rerank(taste, shortlist)
            except SieError as exc:
                log.warning("SIE rerank failed, using SIE cosine only: %s", exc)
                return _sorted(cosine, candidates)
            for event in shortlist:
                final[event.id] += (1.0 - COSINE_WEIGHT) * _to_unit(reranked.get(event.id, 0.0))
        return _sorted(final, candidates)

    def _encode_query(self, taste: str) -> np.ndarray:
        try:
            [vec] = self.client.encode([taste], is_query=True, instruction=self._instruction)
        except SieError as exc:
            if not self._instruction or "HTTP 4" not in str(exc):
                raise
            log.info("SIE rejected the query instruction, retrying without it: %s", exc)
            self._instruction = None
            [vec] = self.client.encode([taste], is_query=True)
        return _unit(vec)

    def _rerank(self, taste: str, events: list[Event]) -> dict[str, float]:
        items = [(e.id, event_text(e)[:1000]) for e in events]
        try:
            return self.client.score(taste, items, instruction=self._instruction)
        except SieError as exc:
            if not self._instruction or "HTTP 4" not in str(exc):
                raise
            self._instruction = None
            return self.client.score(taste, items)

    # ------------------------------------------------ vectors and file cache

    def _vector_key(self, event: Event) -> str:
        return f"{event.id}:{_sig(self.client.embed_model + chr(10) + event_text(event))}"

    def _ensure_vectors(self, events: list[Event], timeout_s: Optional[float] = None) -> dict[str, np.ndarray]:
        self._load_file_cache()
        keys = {e.id: self._vector_key(e) for e in events}
        with self._lock:
            missing = [e for e in events if keys[e.id] not in self._vectors]
        if missing:
            fresh = self.client.encode([event_text(e) for e in missing], timeout_s=timeout_s)
            with self._lock:
                for event, vec in zip(missing, fresh):
                    self._vectors[keys[event.id]] = _unit(vec)
            self._save_file_cache()
        with self._lock:
            return {e.id: self._vectors[keys[e.id]] for e in events}

    def _load_file_cache(self) -> None:
        if self._file_loaded:
            return
        self._file_loaded = True
        if not self._cache_path or not self._cache_path.exists():
            return
        try:
            raw = json.loads(self._cache_path.read_text())
            vectors = {k: _unit(np.asarray(v, dtype=np.float32)) for k, v in raw.get("vectors", {}).items()}
        except (OSError, ValueError, AttributeError) as exc:
            log.warning("Ignoring unreadable SIE cache %s: %s", self._cache_path, exc)
            return
        with self._lock:
            for k, v in vectors.items():
                self._vectors.setdefault(k, v)

    def _save_file_cache(self) -> None:
        if not self._cache_path:
            return
        with self._lock:
            payload = {"version": 1,
                       "vectors": {k: [round(float(x), 6) for x in v] for k, v in self._vectors.items()}}
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self._cache_path)
        except OSError as exc:
            log.warning("Could not write SIE cache %s: %s", self._cache_path, exc)

    def _warn_unconfigured(self) -> None:
        if not self._warned_unconfigured:
            self._warned_unconfigured = True
            log.warning("SIE_API_KEY (or SIE_BASE_URL) is not set; arm sie is using TF-IDF.")


def _unit(vec: np.ndarray) -> np.ndarray:
    vec = np.asarray(vec, dtype=np.float32).ravel()
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 0 else vec
