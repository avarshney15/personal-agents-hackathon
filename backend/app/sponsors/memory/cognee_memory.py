"""Cognee-backed memory for Event Agent. Implements contracts.Memory.

How it works
- Every fact lives in a local SQLite index (MEMORY_DB_PATH, default ./memory.db). That index is
  the source of truth for the Memory screen, forget, and taste.
- Each fact is also stored in Cognee (cognee.remember: add + cognify) in a per-user dataset
  named user_<id>. The document text carries a [fact:<id>] tag so search hits map back to our
  facts, and the Cognee data id is kept so forget can delete that exact copy.
- recall() asks Cognee for the facts nearest the event by meaning (CHUNKS search, no LLM call),
  maps hits back through the local index (so a forgotten fact can never come back), and falls
  back to keyword overlap when Cognee fails, times out, or returns nothing usable.
- Cognee is async. All Cognee calls run on one background event-loop thread, so the sync
  methods are safe from FastAPI worker threads and from inside a running loop.

Keys (first match wins)
- COGNEE_API_KEY + COGNEE_SERVICE_URL: Cognee Cloud (cognee.serve routes remember/recall/forget).
- LLM_API_KEY: local Cognee (cognee reads LLM_API_KEY itself; LLM_PROVIDER, LLM_MODEL and
  EMBEDDING_* are optional overrides, see Cognee's docs).
- COGNEE_KEYLESS=1: local Cognee on local models (fastembed embeddings, GLiNER extraction).
  Downloads about 820 MB of models on first use.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from app.contracts import Event, Fact, FactGroups, FactKind
from app.sponsors.memory.store import FactStore, row_to_fact
from app.sponsors.memory.text_match import event_text, overlap

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[3]
_FACT_TAG = re.compile(r"\[fact:([0-9a-f]{6,32})\]")
_FALLBACK_TTL = 30.0  # seconds a keyword-only answer is cached before Cognee is asked again
_DOWN_SECONDS = 30.0  # after a Cognee search error, skip Cognee for this long


class MemoryConfigError(RuntimeError):
    """CogneeMemory was used without Cognee keys."""


# ---------------------------------------------------------------- config


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    value = _env(name)
    return default if not value else value.lower() in ("1", "true", "yes", "on")


def _backend_path(value: str) -> str:
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (BACKEND_DIR / path).resolve())


def cognee_mode() -> str:
    """'cloud', 'local' or 'keyless'. Raises MemoryConfigError when no keys are set."""
    if _env("COGNEE_API_KEY") or _env("COGNEE_SERVICE_URL"):
        if not _env("COGNEE_SERVICE_URL"):
            raise MemoryConfigError(
                "COGNEE_API_KEY is set but COGNEE_SERVICE_URL is not. Set COGNEE_SERVICE_URL to "
                "your Cognee Cloud tenant URL (Cognee Cloud console, API Keys page), e.g. "
                "https://your-tenant.aws.cognee.ai")
        return "cloud"
    if _env("LLM_API_KEY"):
        return "local"
    if _flag("COGNEE_KEYLESS"):
        return "keyless"
    raise MemoryConfigError(
        "CogneeMemory needs keys. Set one of: COGNEE_API_KEY and COGNEE_SERVICE_URL (Cognee "
        "Cloud); LLM_API_KEY (local Cognee, OpenAI by default); or COGNEE_KEYLESS=1 (local "
        "Cognee on local models, no key). Or keep memory on its fake: SPONSORS_FAKE=1 or "
        "SPONSORS_FAKE=memory.")


def _prepare_cognee_env() -> None:
    """Points Cognee's local storage at COGNEE_DATA_DIR (default ./cognee_data). Must run
    before `import cognee`. Values already in the environment win."""
    data_dir = Path(_backend_path(_env("COGNEE_DATA_DIR", "./cognee_data")))
    data_dir.mkdir(parents=True, exist_ok=True)
    ignore = data_dir / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")
    os.environ.setdefault("DATA_ROOT_DIRECTORY", str(data_dir / "data"))
    os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(data_dir / "system"))
    os.environ.setdefault("CACHE_ROOT_DIRECTORY", str(data_dir / "cache"))


def dataset_name(user_id: str) -> str:
    return "user_" + re.sub(r"[^A-Za-z0-9_]", "_", user_id)


def cognee_document(fact: Fact) -> str:
    """The text stored in Cognee: the sentence itself plus a tag that maps hits back."""
    text = fact.text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} [fact:{fact.id}]"


# ---------------------------------------------------------------- background loop


class _Loop:
    """One process-wide event loop thread for every Cognee call (Cognee's engines and HTTP
    sessions bind to the loop they were first used on)."""

    _instance: Optional["_Loop"] = None
    _guard = threading.Lock()

    @classmethod
    def get(cls) -> "_Loop":
        with cls._guard:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="cognee-loop", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro) -> concurrent.futures.Future:
        if threading.current_thread() is self.thread:
            coro.close()
            raise RuntimeError("CogneeMemory's sync methods can't be called from its own loop thread.")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)


# ---------------------------------------------------------------- cognee calls (loop thread only)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _remember_ids(result: Any) -> tuple[list[str], Optional[str]]:
    """(item ids, dataset_id) from a RememberResult (local) or its JSON dict (cloud).
    The items list every data item in the dataset's run, not only the new one."""
    status = _get(result, "status")
    if status == "errored":
        raise RuntimeError(_get(result, "error") or "Cognee remember errored")
    items = _get(result, "items") or []
    ids = [str(_get(i, "id")) for i in items if _get(i, "id")]
    dataset_id = _get(result, "dataset_id")
    return ids, (str(dataset_id) if dataset_id else None)


Hit = tuple[str, Optional[float], Optional[str]]  # (chunk text, distance, Cognee data id)


def _hit(item: Any) -> Hit:
    """From a recall result (pydantic model locally, dict from the cloud). For CHUNKS the
    score is a cosine distance: lower is closer."""
    raw = _get(item, "raw") or {}
    text = _get(item, "text") or (raw.get("text", "") if isinstance(raw, dict) else "")
    score = _get(item, "score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    metadata = _get(item, "metadata") or {}
    data_id = metadata.get("data_id") if isinstance(metadata, dict) else None
    return str(text), score, (str(data_id) if data_id else None)


def _missing_dataset(exc: BaseException) -> bool:
    name = type(exc).__name__
    text = str(exc).lower()
    return (name in ("DatasetNotFoundError", "DatabaseNotCreatedError", "NoDataError")
            or "not found" in text or "404" in text or "does not exist" in text)


class _Cognee:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._module: Any = None
        self._init_lock: Optional[asyncio.Lock] = None
        self._write_lock: Optional[asyncio.Lock] = None
        self._read_slots: Optional[asyncio.Semaphore] = None
        self._seen: dict[str, set[str]] = {}  # dataset -> data ids already accounted for
        self.last_error: Optional[str] = None

    async def module(self) -> Any:
        if self._module is not None:
            return self._module
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            if self._module is None:
                _prepare_cognee_env()
                import cognee  # lazy: heavy import, and only needed with keys

                if self.mode == "cloud":
                    await cognee.serve(url=_env("COGNEE_SERVICE_URL"),
                                       api_key=_env("COGNEE_API_KEY") or None)
                self._write_lock = asyncio.Lock()
                self._read_slots = asyncio.Semaphore(int(_env("COGNEE_RECALL_CONCURRENCY", "8")))
                self._module = cognee
        return self._module

    async def add(self, dataset: str, document: str,
                  known: set[str]) -> tuple[Optional[str], Optional[str]]:
        """Stores one document; returns (its data id, dataset id). `known` holds data ids
        already mapped to other facts, so the new id is the one nobody claims yet."""
        cognee = await self.module()
        async with self._write_lock:
            result = await cognee.remember(document, dataset_name=dataset, self_improvement=False)
            ids, dataset_id = _remember_ids(result)
            seen = self._seen.setdefault(dataset, set())
            new = [i for i in ids if i not in known and i not in seen]
            seen.update(ids)
        if new:
            return new[-1], dataset_id
        return (ids[0] if len(ids) == 1 else None), dataset_id

    async def search(self, dataset: str, query: str, top_k: int) -> list[Hit]:
        cognee = await self.module()
        async with self._read_slots:
            try:
                results = await cognee.recall(query, query_type=cognee.SearchType.CHUNKS,
                                              datasets=[dataset], top_k=top_k)
            except Exception as exc:  # a user with nothing in Cognee yet has no dataset
                if _missing_dataset(exc):
                    return []
                raise
        return [_hit(r) for r in results or [] if _get(r, "source", "graph") == "graph"]

    async def search_many(self, dataset: str, queries: dict[str, str],
                          top_k: int) -> dict[str, Optional[list[Hit]]]:
        keys = list(queries)
        found = await asyncio.gather(*(self.search(dataset, queries[k], top_k) for k in keys),
                                     return_exceptions=True)
        out: dict[str, Optional[list]] = {}
        for key, value in zip(keys, found):
            if isinstance(value, BaseException):
                self.last_error = f"{type(value).__name__}: {value}"
                log.warning("Cognee search failed: %s", self.last_error)
                out[key] = None
            else:
                out[key] = value
        return out

    async def forget_item(self, dataset: str, data_id: str) -> None:
        from uuid import UUID

        cognee = await self.module()
        async with self._write_lock:
            try:
                await cognee.forget(data_id=UUID(data_id), dataset=dataset)
            except Exception as exc:
                if not _missing_dataset(exc):
                    raise

    async def forget_dataset(self, dataset: str) -> None:
        cognee = await self.module()
        async with self._write_lock:
            try:
                await cognee.forget(dataset=dataset)
            except Exception as exc:
                if not _missing_dataset(exc):
                    raise
            self._seen.pop(dataset, None)


# ---------------------------------------------------------------- memory


class CogneeMemory:
    def __init__(self, db_path: Optional[str] = None, *, recall_timeout: Optional[float] = None,
                 sync_writes: Optional[bool] = None, warmup: Optional[bool] = None) -> None:
        self._store = FactStore(db_path or _backend_path(_env("MEMORY_DB_PATH", "./memory.db")))
        self._recall_timeout = (recall_timeout if recall_timeout is not None
                                else float(_env("COGNEE_RECALL_TIMEOUT", "6")))
        self._sync_writes = sync_writes if sync_writes is not None else _flag("COGNEE_SYNC_WRITES")
        self._write_timeout = float(_env("COGNEE_WRITE_TIMEOUT", "180"))
        max_distance = _env("COGNEE_RECALL_MAX_DISTANCE")
        self._max_distance = float(max_distance) if max_distance else None  # default set per mode
        self._margin = float(_env("COGNEE_RECALL_MARGIN", "0.12"))
        self._lock = threading.Lock()
        self._versions: dict[str, int] = {}
        self._cache: dict[tuple[str, str, int], tuple[int, float, list[str]]] = {}
        self._pending: set[concurrent.futures.Future] = set()
        self._cognee: Optional[_Cognee] = None
        self._down_until = 0.0
        self.last_error: Optional[str] = None
        if warmup if warmup is not None else _flag("COGNEE_WARMUP", True):
            try:
                self._submit(self._backend().module())  # import cognee before the first feed
            except MemoryConfigError:
                pass  # raised again, clearly, on first use

    # ------------------------------------------------ Memory protocol

    def remember(self, user_id: str, kind: FactKind, text: str, from_text: str,
                 event_id: Optional[str] = None) -> str:
        backend = self._backend()
        fact = Fact(id=uuid.uuid4().hex[:12], kind=kind, text=text.strip(),
                    from_text=from_text.strip(), event_id=event_id,
                    created_at=datetime.now(timezone.utc))
        self._store.add(user_id, fact)
        self._changed(user_id)
        future = self._submit(self._ingest(backend, user_id, fact))
        if self._sync_writes:
            self._wait(future, self._write_timeout)
        return fact.id

    def recall(self, user_id: str, event: Event, k: int = 3) -> list[Fact]:
        return self.recall_many(user_id, [event], k).get(event.id, [])

    def facts(self, user_id: str) -> FactGroups:
        groups = FactGroups()
        for fact in self._store.facts(user_id):  # newest first
            bucket = "skipped" if fact.kind == "hidden" else fact.kind
            getattr(groups, bucket).append(fact)
        return groups

    def forget(self, user_id: str, fact_id: str) -> None:
        backend = self._backend()
        row = self._store.delete(user_id, fact_id)  # stops counting here: recall maps through it
        self._changed(user_id)
        if row and row.get("cognee_data_id"):
            future = self._submit(backend.forget_item(dataset_name(user_id), row["cognee_data_id"]))
            if self._sync_writes:
                self._wait(future, self._write_timeout)
        # A fact still being indexed is deleted from Cognee when its ingest finishes.

    def forget_all(self, user_id: str) -> None:
        backend = self._backend()
        self._store.delete_all(user_id)
        self._changed(user_id)
        future = self._submit(backend.forget_dataset(dataset_name(user_id)))
        if self._sync_writes:
            self._wait(future, self._write_timeout)

    def taste(self, user_id: str) -> str:
        facts = self._store.facts(user_id)
        said = [f.text for f in facts if f.kind == "said"]
        went = [f.text for f in facts if f.kind == "went"]
        return "; ".join(said + went)

    # ------------------------------------------------ extras (not in the protocol)

    def recall_many(self, user_id: str, events: Iterable[Event], k: int = 3) -> dict[str, list[Fact]]:
        """recall() for a whole feed in one round trip: Cognee searches run concurrently.
        Returns {event_id: facts}."""
        backend = self._backend()
        events = list(events)
        rows = self._store.rows(user_id)
        if not rows or k <= 0:
            return {e.id: [] for e in events}
        facts = [row_to_fact(r) for r in rows]
        by_id = {f.id: f for f in facts}
        unindexed = {r["id"] for r in rows if r["cognee_status"] != "indexed"}
        version = self._version(user_id)

        out: dict[str, list[Fact]] = {}
        misses: list[Event] = []
        for event in events:
            ids = self._cache_get(user_id, event.id, k, version)
            if ids is None:
                misses.append(event)
            else:
                out[event.id] = [by_id[i] for i in ids if i in by_id][:k]

        if misses:
            searched = self._search(backend, user_id, misses, facts, unindexed, k, version)
            for event in misses:
                cognee_ids = searched.get(event.id)
                ids = self._rank(cognee_ids, event, facts, unindexed, k)
                ttl = None if cognee_ids else _FALLBACK_TTL
                self._cache_put(user_id, event.id, k, version, ids, ttl)
                out[event.id] = [by_id[i] for i in ids]
        return out

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """Blocks until queued Cognee writes finish. True if everything finished."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                pending = [f for f in self._pending if not f.done()]
            if not pending:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            concurrent.futures.wait(pending, timeout=remaining)

    def status(self) -> dict:
        try:
            mode = cognee_mode()
        except MemoryConfigError as exc:
            mode = f"not configured: {exc}"
        with self._lock:
            pending = sum(1 for f in self._pending if not f.done())
        return {"mode": mode, "facts": self._store.status_counts(), "pending_writes": pending,
                "last_error": self.last_error, "db_path": self._store.path}

    # ------------------------------------------------ internals

    def _backend(self) -> _Cognee:
        if self._cognee is None:
            mode = cognee_mode()  # raises MemoryConfigError without keys
            with self._lock:
                if self._cognee is None:
                    self._cognee = _Cognee(mode)
        return self._cognee

    def _submit(self, coro) -> concurrent.futures.Future:
        future = _Loop.get().submit(coro)
        with self._lock:
            self._pending.add(future)
        future.add_done_callback(self._finished)
        return future

    def _finished(self, future: concurrent.futures.Future) -> None:
        with self._lock:
            self._pending.discard(future)
        if not future.cancelled() and future.exception() is not None:
            exc = future.exception()
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Cognee call failed: %s", self.last_error)

    def _wait(self, future: concurrent.futures.Future, timeout: float) -> None:
        try:
            future.result(timeout)
        except Exception as exc:  # the local index already has the change
            log.warning("Cognee write did not finish: %s: %s", type(exc).__name__, exc)

    async def _ingest(self, backend: _Cognee, user_id: str, fact: Fact) -> None:
        if self._store.get(user_id, fact.id) is None:
            return  # forgotten before it reached Cognee
        dataset = dataset_name(user_id)
        try:
            data_id, dataset_id = await backend.add(dataset, cognee_document(fact),
                                                    self._store.indexed_data_ids(user_id))
        except Exception as exc:
            self._store.mark_failed(fact.id, f"{type(exc).__name__}: {exc}")
            raise
        if not self._store.mark_indexed(fact.id, data_id, dataset_id):
            if data_id:  # forgotten while Cognee indexed it: delete that copy now
                await backend.forget_item(dataset, data_id)
            return
        self._changed(user_id)  # Cognee can find it now: refresh cached recalls

    def _search(self, backend: _Cognee, user_id: str, events: list[Event], facts: list[Fact],
                unindexed: set[str], k: int, version: int) -> dict[str, Optional[list[str]]]:
        """{event_id: fact ids from Cognee, best first}, or {} if Cognee is unavailable."""
        if time.monotonic() < self._down_until or len(unindexed) == len(facts):
            return {}  # Cognee is failing, or holds none of this user's facts yet
        queries = {e.id: event_text(e) for e in events}
        top_k = max(k * 3, 10)
        future = self._submit(backend.search_many(dataset_name(user_id), queries, top_k))

        def mapped(results: dict) -> dict[str, Optional[list[str]]]:
            return {eid: (self._map_hits(hits, facts) if hits is not None else None)
                    for eid, hits in results.items()}

        try:
            results = future.result(self._recall_timeout)
        except concurrent.futures.TimeoutError:
            # Let it finish in the background and fill the cache for the next feed.
            def fill(done: concurrent.futures.Future) -> None:
                if done.cancelled() or done.exception() is not None:
                    return
                for event in events:
                    ids = mapped(done.result()).get(event.id)
                    if ids:
                        self._cache_put(user_id, event.id, k, version,
                                        self._rank(ids, event, facts, unindexed, k), None)

            future.add_done_callback(fill)
            log.info("Cognee recall slower than %.1fs; using keywords this time", self._recall_timeout)
            return {}
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._down_until = time.monotonic() + _DOWN_SECONDS
            log.warning("Cognee recall failed, using keywords: %s", self.last_error)
            return {}
        if results and all(v is None for v in results.values()):
            self.last_error = backend.last_error
            self._down_until = time.monotonic() + _DOWN_SECONDS
        return mapped(results)

    def _map_hits(self, hits: list[Hit], facts: list[Fact]) -> list[str]:
        """Cognee hits -> our fact ids, closest first. Keeps hits under the distance cap and
        within a margin of the closest one; drops anything not in the local index."""
        known = {f.id for f in facts}
        by_text = {_norm(f.text): f.id for f in facts}
        hits = sorted(hits, key=lambda h: (h[1] is None, h[1] if h[1] is not None else 0.0))
        best = next((h[1] for h in hits if h[1] is not None), None)
        cap = self._max_distance if self._max_distance is not None else _default_max_distance(
            self._cognee.mode if self._cognee else "")
        ids: list[str] = []
        for text, score, data_id in hits:
            if score is not None and (score > cap or score > best + self._margin):
                continue
            found = _FACT_TAG.findall(text) or [by_text.get(_norm(_FACT_TAG.sub("", text)))]
            for fact_id in found:
                if fact_id and fact_id in known and fact_id not in ids:
                    ids.append(fact_id)
                    if data_id and len(found) == 1:
                        self._store.set_data_id(fact_id, data_id)
        return ids

    def _rank(self, cognee_ids: Optional[list[str]], event: Event, facts: list[Fact],
              unindexed: set[str], k: int) -> list[str]:
        """Cognee's picks (by meaning) first, then facts sharing words with the event (this also
        covers facts Cognee hasn't indexed yet). With nothing from Cognee, keywords alone."""
        ids = list(cognee_ids or [])
        pool = [f for f in facts if f.id not in ids]
        scored = [(overlap(f.text, event), f) for f in pool]
        scored = sorted((p for p in scored if p[0] > 0), key=lambda p: -p[0])  # stable: newest first
        ids += [f.id for _, f in scored]
        return ids[:k]

    def _version(self, user_id: str) -> int:
        with self._lock:
            return self._versions.get(user_id, 0)

    def _changed(self, user_id: str) -> None:
        with self._lock:
            self._versions[user_id] = self._versions.get(user_id, 0) + 1
            for key in [key for key in self._cache if key[0] == user_id]:
                del self._cache[key]

    def _cache_get(self, user_id: str, event_id: str, k: int, version: int) -> Optional[list[str]]:
        with self._lock:
            entry = self._cache.get((user_id, event_id, k))
        if not entry:
            return None
        cached_version, expires, ids = entry
        if cached_version != version or time.monotonic() > expires:
            return None
        return ids

    def _cache_put(self, user_id: str, event_id: str, k: int, version: int, ids: list[str],
                   ttl: Optional[float]) -> None:
        expires = time.monotonic() + ttl if ttl else float("inf")
        with self._lock:
            if self._versions.get(user_id, 0) == version:  # memory changed meanwhile: don't store
                self._cache[(user_id, event_id, k)] = (version, expires, list(ids))


def _default_max_distance(mode: str) -> float:
    # Measured on the keyless model (bge-small): related 0.21-0.28, unrelated 0.37-0.46.
    # OpenAI's larger embeddings spread wider, so the cap is looser there.
    return 0.40 if mode == "keyless" else 0.75


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!? ")
