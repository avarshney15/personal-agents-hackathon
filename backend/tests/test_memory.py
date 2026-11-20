"""Part 1 (memory): FakeMemory and CogneeMemory against the Memory contract.

CogneeMemory runs with a stand-in `cognee` module (put in sys.modules), shaped like the real
cognee 1.6.10 calls it uses: remember -> RememberResult with every item id in the dataset,
recall(CHUNKS) -> hits with text, a cosine distance and metadata.data_id, forget by data id or
by dataset. Real cognee was checked by hand (see the module docstring for keys).
"""

from __future__ import annotations

import asyncio
import re
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import contracts as c
from app.demo import seed_demo_memory
from app.sponsors.memory import cognee_memory as cm
from app.sponsors.memory.cognee_memory import CogneeMemory, MemoryConfigError
from app.sponsors.memory.fake import FakeMemory

USER = "u1"


def event(eid: str, title: str, description: str = "", tags=(), host: str = "",
          days: int = 2, hour_utc: int = 3) -> c.Event:
    start = datetime(2026, 11, 24, hour_utc, tzinfo=timezone.utc) + timedelta(days=days)
    return c.Event(id=eid, title=title, description=description, tags=list(tags), host=host,
                   starts_at=start, url="https://lu.ma/x")


BOULDERING = event("ev-boulder", "Bouldering social", "Climbing meetup at Dogpatch Boulders.",
                   ["climbing", "social"])
CLIMB_GYM = event("ev-gym", "Climbing gym open house", "Try the walls.", ["fitness"])
AGENTS = event("ev-agents", "Agent builders night", "Hands-on night for people building AI agents.",
               ["AI agents", "hackathon"], host="Agent Builders SF")
OPERA = event("ev-opera", "Opera matinee", "Puccini at the War Memorial.", ["music"])


# ---------------------------------------------------------------- stand-in cognee

_CONCEPTS = {"climb": {"climbing", "bouldering", "boulders", "boulder", "climb", "walls"},
             "agents": {"agents", "agent", "hackathons", "hackathon", "builders"},
             "sales": {"sales", "pitch", "pitches"}}


def _concepts(text: str) -> set[str]:
    words = set(re.findall(r"[a-z]+", text.lower()))
    return {name for name, vocab in _CONCEPTS.items() if words & vocab}


class DatasetNotFoundError(Exception):
    pass


class FakeCognee(types.ModuleType):
    """Records calls. Datasets: name -> {data_id: text}."""

    class SearchType:
        CHUNKS = "CHUNKS"

    def __init__(self, return_dicts: bool = False):
        super().__init__("cognee")
        self.datasets: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []
        self.return_dicts = return_dicts
        self.fail_recall = False
        self.gate: asyncio.Event | None = None  # when set, remember waits for it

    async def serve(self, url=None, api_key=None):
        self.calls.append("serve")

    async def remember(self, data, dataset_name="main_dataset", **kwargs):
        self.calls.append("remember")
        assert kwargs.get("self_improvement") is False
        if self.gate is not None:
            await self.gate.wait()
        docs = self.datasets.setdefault(dataset_name, {})
        docs[str(uuid.uuid4())] = data
        items = [{"id": i} for i in docs]  # like real cognee: every item in the dataset
        result = {"status": "completed", "dataset_id": "ds-" + dataset_name, "items": items}
        return result if self.return_dicts else types.SimpleNamespace(**result)

    async def recall(self, query, query_type=None, datasets=None, top_k=15, **kwargs):
        self.calls.append("recall")
        assert query_type == "CHUNKS"
        if self.fail_recall:
            raise RuntimeError("cognee is down")
        name = datasets[0]
        if name not in self.datasets:
            raise DatasetNotFoundError(f"Dataset(s) not found: '{name}'")
        want = _concepts(query)
        hits = [types.SimpleNamespace(source="graph", text=text, metadata={"data_id": data_id},
                                      score=0.2 if _concepts(text) & want else 0.9)
                for data_id, text in self.datasets[name].items()]
        return sorted(hits, key=lambda h: h.score)[:top_k]

    async def forget(self, data_id=None, dataset=None, **kwargs):
        self.calls.append("forget")
        if dataset not in self.datasets:
            raise DatasetNotFoundError(f"Dataset(s) not found: '{dataset}'")
        if data_id is not None:
            self.datasets[dataset].pop(str(data_id), None)
        else:
            del self.datasets[dataset]
        return {"status": "success"}

    def texts(self, user_id: str = USER) -> list[str]:
        return sorted(self.datasets.get(cm.dataset_name(user_id), {}).values())


@pytest.fixture
def cognee_env(monkeypatch, tmp_path):
    for key in ("COGNEE_API_KEY", "COGNEE_SERVICE_URL", "COGNEE_KEYLESS", "COGNEE_SYNC_WRITES",
                "COGNEE_RECALL_MAX_DISTANCE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("COGNEE_DATA_DIR", str(tmp_path / "cognee"))
    for key in ("DATA_ROOT_DIRECTORY", "SYSTEM_ROOT_DIRECTORY", "CACHE_ROOT_DIRECTORY"):
        monkeypatch.setenv(key, str(tmp_path / key.lower()))
    fake = FakeCognee()
    monkeypatch.setitem(sys.modules, "cognee", fake)
    return fake


def make_cognee(tmp_path, **kwargs) -> CogneeMemory:
    kwargs.setdefault("sync_writes", True)
    kwargs.setdefault("warmup", False)
    return CogneeMemory(str(tmp_path / "memory.db"), **kwargs)


@pytest.fixture(params=["fake", "cognee"])
def memory(request, tmp_path):
    if request.param == "fake":
        return FakeMemory()
    request.getfixturevalue("cognee_env")
    return make_cognee(tmp_path)


# ---------------------------------------------------------------- contract, both implementations


def test_matches_interface(memory):
    assert isinstance(memory, c.Memory)


def test_remember_groups_newest_first(memory):
    a = memory.remember(USER, "said", "Into climbing lately", "You told the agent, Nov 18")
    b = memory.remember(USER, "went", "Likes hands-on hackathons over talks", "3 hackathons went")
    d = memory.remember(USER, "said", "Weekend afternoons stay free", "You told the agent, Oct 2")
    e = memory.remember(USER, "skipped", "Not into sales and pitch nights", "Hid 4 of them")
    f = memory.remember(USER, "hidden", "No crypto meetups", "You hid one", event_id="ev-crypto")
    groups = memory.facts(USER)
    assert [x.id for x in groups.said] == [d, a]
    assert [x.id for x in groups.went] == [b]
    assert [x.id for x in groups.skipped] == [f, e]  # hidden folds into skipped
    assert groups.skipped[0].kind == "hidden" and groups.skipped[0].event_id == "ev-crypto"
    assert groups.said[1].from_text == "You told the agent, Nov 18"
    assert memory.facts("someone-else") == c.FactGroups()


def test_recall_returns_relevant_facts(memory):
    climb = memory.remember(USER, "said", "Into climbing and bouldering lately", "You told the agent")
    hack = memory.remember(USER, "went", "Likes hands-on hackathons over talks", "3 hackathons went")
    memory.remember(USER, "skipped", "Not into sales and pitch nights", "Hid 4 of them")
    assert [f.id for f in memory.recall(USER, BOULDERING)][:1] == [climb]
    assert hack in [f.id for f in memory.recall(USER, AGENTS)]
    assert memory.recall(USER, OPERA) == []
    assert len(memory.recall(USER, AGENTS, k=1)) == 1
    assert memory.recall("nobody", AGENTS) == []


def test_forget_removes_from_next_recall(memory):
    climb = memory.remember(USER, "said", "Into climbing and bouldering lately", "You told the agent")
    other = memory.remember(USER, "went", "Goes to climbing nights at Dogpatch", "Went twice")
    assert climb in [f.id for f in memory.recall(USER, BOULDERING)]
    memory.forget(USER, climb)
    assert climb not in [f.id for f in memory.recall(USER, BOULDERING)]
    assert other in [f.id for f in memory.recall(USER, BOULDERING)]
    assert climb not in [f.id for f in memory.facts(USER).said]
    memory.forget(USER, "no-such-fact")  # no error


def test_forget_all_empties(memory):
    memory.remember(USER, "said", "Into climbing and bouldering lately", "You told the agent")
    memory.remember(USER, "went", "Likes hands-on hackathons over talks", "3 hackathons went")
    memory.remember("u2", "said", "Into climbing too", "You told the agent")
    memory.forget_all(USER)
    assert memory.facts(USER) == c.FactGroups()
    assert memory.recall(USER, BOULDERING) == []
    assert memory.taste(USER) == ""
    assert memory.facts("u2").said  # other users untouched
    memory.forget_all(USER)  # twice is fine


def test_taste_joins_said_and_went(memory):
    memory.remember(USER, "said", "Into climbing lately", "You told the agent")
    memory.remember(USER, "went", "Likes hands-on hackathons over talks", "3 hackathons went")
    memory.remember(USER, "skipped", "Not into sales and pitch nights", "Hid 4 of them")
    taste = memory.taste(USER)
    assert "Into climbing lately" in taste and "Likes hands-on hackathons over talks" in taste
    assert "sales" not in taste
    assert memory.taste("nobody") == ""


def test_demo_seed_and_recall(memory):
    seed_demo_memory(memory)
    groups = memory.facts(c.DEMO_USER_ID)
    assert len(groups.went) == 2 and len(groups.said) == 1 and len(groups.skipped) == 1
    texts = [f.text for f in memory.recall(c.DEMO_USER_ID, AGENTS)]
    assert any("Agent Builders" in t or "hackathons" in t for t in texts)


def test_recall_many(memory):
    climb = memory.remember(USER, "said", "Into climbing and bouldering lately", "You told the agent")
    out = memory.recall_many(USER, [BOULDERING, OPERA])
    assert [f.id for f in out["ev-boulder"]][:1] == [climb] and out["ev-opera"] == []


# ---------------------------------------------------------------- CogneeMemory specifics


def test_cognee_stores_and_maps_each_fact(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    ids = [memory.remember(USER, "said", t, "You told the agent")
           for t in ("Into bouldering lately", "Likes hackathons", "Not into sales pitches")]
    assert len(cognee_env.texts()) == 3
    assert all(f"[fact:{i}]" in " ".join(cognee_env.texts()) for i in ids)
    rows = {r["id"]: r for r in memory._store.rows(USER)}
    assert all(r["cognee_status"] == "indexed" for r in rows.values())
    assert len({r["cognee_data_id"] for r in rows.values()}) == 3  # each fact its own data id
    # Forgetting the middle fact deletes exactly its document in Cognee.
    memory.forget(USER, ids[1])
    memory.wait_idle(5)
    assert cognee_env.texts() == sorted([f"Into bouldering lately. [fact:{ids[0]}]",
                                         f"Not into sales pitches. [fact:{ids[2]}]"])


def test_cognee_recall_is_by_meaning(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    climb = memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    memory.remember(USER, "said", "Likes hackathons", "You told the agent")
    # "Climbing gym open house" shares no word with "Into bouldering lately".
    assert [f.id for f in memory.recall(USER, CLIMB_GYM)] == [climb]
    assert [f.id for f in FakeMemory().recall(USER, CLIMB_GYM)] == []


def test_cognee_falls_back_to_keywords(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    climb = memory.remember(USER, "said", "Into climbing lately", "You told the agent")
    cognee_env.fail_recall = True
    assert [f.id for f in memory.recall(USER, BOULDERING)] == [climb]
    assert memory.last_error and "cognee is down" in memory.last_error


def test_cognee_recall_is_cached_until_memory_changes(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    memory.recall(USER, BOULDERING)
    memory.recall(USER, BOULDERING)
    assert cognee_env.calls.count("recall") == 1
    memory.remember(USER, "went", "Climbs at Dogpatch", "Went twice")
    memory.recall(USER, BOULDERING)
    assert cognee_env.calls.count("recall") == 2
    memory.recall_many(USER, [BOULDERING, CLIMB_GYM, AGENTS])
    assert cognee_env.calls.count("recall") == 4  # only the two new events


def test_cognee_forget_while_indexing(cognee_env, tmp_path):
    memory = make_cognee(tmp_path, sync_writes=False)
    loop = cm._Loop.get().loop
    gate = asyncio.run_coroutine_threadsafe(_make_event(), loop).result(5)
    cognee_env.gate = gate
    fid = memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    # Not in Cognee yet, but recall already finds it by keywords.
    assert [f.id for f in memory.recall(USER, BOULDERING)] == [fid]
    memory.forget(USER, fid)
    assert memory.recall(USER, BOULDERING) == []
    loop.call_soon_threadsafe(gate.set)
    assert memory.wait_idle(5)
    assert cognee_env.texts() == []  # the copy indexed after the forget was deleted


async def _make_event() -> asyncio.Event:
    return asyncio.Event()


def test_cognee_forget_all_drops_dataset(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    memory.forget_all(USER)
    assert cm.dataset_name(USER) not in cognee_env.datasets
    assert memory.recall(USER, BOULDERING) == []
    memory.remember(USER, "said", "Into bouldering again", "You told the agent")
    assert [f.text for f in memory.recall(USER, BOULDERING)] == ["Into bouldering again"]


def test_cognee_cloud_dict_results(cognee_env, tmp_path, monkeypatch):
    monkeypatch.setenv("COGNEE_API_KEY", "ck_test")
    monkeypatch.setenv("COGNEE_SERVICE_URL", "https://tenant.example")
    cognee_env.return_dicts = True
    memory = make_cognee(tmp_path)
    fid = memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    assert cognee_env.calls[0] == "serve"
    assert [f.id for f in memory.recall(USER, CLIMB_GYM)] == [fid]


def test_cognee_persists_across_instances(cognee_env, tmp_path):
    first = make_cognee(tmp_path)
    fid = first.remember(USER, "went", "Likes hands-on hackathons", "3 hackathons went")
    second = make_cognee(tmp_path)
    assert [f.id for f in second.facts(USER).went] == [fid]


def test_cognee_safe_inside_running_loop(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)

    async def handler():  # e.g. an async FastAPI route calling the sync interface
        fid = memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
        return fid, memory.recall(USER, CLIMB_GYM)

    fid, facts = asyncio.run(handler())
    assert [f.id for f in facts] == [fid]


def test_cognee_without_keys_raises_clearly(cognee_env, tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY")
    memory = make_cognee(tmp_path, warmup=True)  # constructing never raises
    assert memory.facts(USER) == c.FactGroups()
    with pytest.raises(MemoryConfigError, match="COGNEE_API_KEY"):
        memory.remember(USER, "said", "Into bouldering lately", "You told the agent")
    monkeypatch.setenv("COGNEE_API_KEY", "ck_test")
    with pytest.raises(MemoryConfigError, match="COGNEE_SERVICE_URL"):
        cm.cognee_mode()


def test_cognee_rejects_bad_kind(cognee_env, tmp_path):
    memory = make_cognee(tmp_path)
    with pytest.raises(ValueError):
        memory.remember(USER, "loved", "Into bouldering lately", "You told the agent")
    assert memory.facts(USER) == c.FactGroups()
