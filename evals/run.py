"""Replays the demo against the running API for N synthetic users.

Each user has a hidden taste (a persona). The script walks the app the way the demo does:
open For you, say what they're into, open the first pick that fits their taste, RSVP and poll
the run to the end, then forget a fact. Every step goes through the real routes, so it fills
the Amplitude charts and the Statsig `ranking` experiment before the demo, and it doubles as a
ranking eval: `hit@5` is the share of users whose top five picks held an event that fits them.

    cd backend && .venv/bin/python ../evals/run.py --users 20
    EVAL_API_URL=http://localhost:8000 .venv/bin/python ../evals/run.py --users 40 --concurrency 6

Synthetic users are sent as the `X-User-Id` header (see backend/API.md, "Current user").
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Optional

import httpx

FINAL = ("done", "needs_you", "failed")
REHEARSALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rehearsals.json")
ARMS = ("tfidf", "sie")

# (what they tell the agent, words that make an event fit them)
PERSONAS: list[tuple[str, list[str]]] = [
    ("AI agents and LLM hackathons", ["agent", "agents", "ai", "llm", "hackathon", "claude"]),
    ("Climbing and bouldering with friends", ["climb", "climbing", "boulder", "bouldering"]),
    ("Live jazz and small music venues", ["jazz", "music", "concert", "live music", "dj"]),
    ("Founder dinners and startup demos", ["founder", "founders", "startup", "demo day", "vc"]),
    ("Trail running and weekend hikes", ["run", "running", "hike", "hiking", "outdoor", "trail"]),
    ("Design systems and UX research", ["design", "ux", "figma", "product design"]),
    ("Climate tech and clean energy", ["climate", "energy", "solar", "sustainab"]),
    ("Robotics and hardware hacking", ["robot", "robotics", "hardware", "maker"]),
    ("Book clubs and writing workshops", ["book", "books", "writing", "reading", "poetry"]),
    ("Open source and developer tools", ["open source", "developer", "devtools", "python", "rust"]),
]

EXTRA_ROUTES = (  # Part 8's routes; skipped when they aren't mounted yet
    ("POST", "/api/taste", "taste"),
    ("POST", "/api/ask", "ask"),
    ("GET", "/api/week", None),
    ("GET", "/api/activity", None),
)


@dataclass
class UserResult:
    user_id: str
    persona: str
    arm: Optional[str] = None
    verified: bool = False
    picks: int = 0
    hit_rank: Optional[int] = None  # 1-based rank of the first pick that fits, within top k
    opened: Optional[str] = None
    run_id: Optional[str] = None
    status: Optional[str] = None  # final run status, or None when no RSVP was started
    stop_reason: Optional[str] = None
    cost_usd: float = 0.0
    model: Optional[str] = None
    site: Optional[str] = None
    forgot: bool = False
    seconds: float = 0.0
    skipped: list[str] = field(default_factory=list)  # routes not there yet
    errors: list[str] = field(default_factory=list)


@dataclass
class Options:
    top_k: int = 5
    always_rsvp: bool = False
    rsvp: bool = True
    run_timeout: float = 180.0
    poll_every: float = 1.0
    answer: Optional[str] = None  # code to answer a "needs you" prompt with; else cancel
    extras: bool = True


def fits(pick: dict, words: list[str]) -> bool:
    event = pick.get("event") or {}
    text = " ".join([event.get("title", ""), event.get("description", ""), event.get("host", ""),
                     " ".join(event.get("tags") or [])]).lower()
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


def _call(client: httpx.Client, result: UserResult, step: str, method: str, path: str,
          **kwargs) -> Optional[httpx.Response]:
    try:
        response = client.request(method, path, headers={"X-User-Id": result.user_id}, **kwargs)
    except httpx.HTTPError as exc:
        result.errors.append(f"{step}: {type(exc).__name__}: {exc}")
        return None
    if response.status_code >= 400:
        result.errors.append(f"{step}: HTTP {response.status_code} {response.text[:120]}")
    return response


def _ok(response: Optional[httpx.Response]) -> bool:
    return response is not None and response.status_code < 400


def run_user(client: httpx.Client, user_id: str, persona: tuple[str, list[str]],
             opts: Options, sleep=time.sleep) -> UserResult:
    into, words = persona
    result = UserResult(user_id=user_id, persona=into)
    started = time.monotonic()
    try:
        _scenario(client, result, into, words, opts, sleep)
    except Exception as exc:  # one bad user never stops the run
        result.errors.append(f"crash: {type(exc).__name__}: {exc}")
    result.seconds = round(time.monotonic() - started, 1)
    return result


def _scenario(client, result: UserResult, into: str, words: list[str], opts: Options, sleep):
    _call(client, result, "me", "GET", "/api/me")

    # Verify once (Persona; the fake passes as soon as it's started).
    if _ok(_call(client, result, "verify_start", "POST", "/api/verify/start")):
        status = _call(client, result, "verify_status", "GET", "/api/verify/status")
        result.verified = bool(_ok(status) and status.json().get("verified"))

    # For you, then "into right now".
    feed = _call(client, result, "feed", "GET", "/api/feed")
    if _ok(feed):
        result.arm = feed.json().get("arm")
    added = _call(client, result, "into", "POST", "/api/into", json={"text": into})
    if _ok(added):
        feed = added
        result.arm = added.json().get("arm") or result.arm

    if opts.extras:
        _extras(client, result, into, words)

    picks = feed.json().get("picks", []) if _ok(feed) else []
    result.picks = len(picks)
    if not picks:
        return

    # Open the first pick in the top k that fits; with no fit, open the top pick and pass.
    chosen = picks[0]
    for rank, pick in enumerate(picks[: opts.top_k], start=1):
        if fits(pick, words):
            result.hit_rank, chosen = rank, pick
            break
    event_id = chosen["event"]["id"]
    if _ok(_call(client, result, "open_pick", "GET", f"/api/events/{event_id}")):
        result.opened = event_id

    if opts.rsvp and result.opened and (result.hit_rank or opts.always_rsvp):
        _rsvp(client, result, event_id, opts, sleep)

    _forget_one(client, result, into)


def _extras(client, result: UserResult, into: str, words: list[str]) -> None:
    bodies = {"taste": {"text": into, "chips": [w.title() for w in words[:2]]},
              "ask": {"text": f"Something for me next week: {into.lower()}"}}
    for method, path, body_key in EXTRA_ROUTES:
        try:
            response = client.request(method, path, headers={"X-User-Id": result.user_id},
                                      json=bodies[body_key] if body_key else None)
        except httpx.HTTPError as exc:
            result.errors.append(f"{path}: {type(exc).__name__}")
            continue
        if response.status_code in (404, 405, 501, 503):
            result.skipped.append(path)
        elif response.status_code >= 400:
            result.errors.append(f"{path}: HTTP {response.status_code}")


def _rsvp(client, result: UserResult, event_id: str, opts: Options, sleep) -> None:
    started = _call(client, result, "rsvp", "POST", "/api/rsvp", json={"event_id": event_id})
    if not _ok(started):
        if started is not None and started.status_code == 403:
            result.status = "unverified"
        return
    run = started.json()
    result.run_id = run["id"]
    deadline = time.monotonic() + opts.run_timeout
    answered = False
    while True:
        status = run.get("status")
        waiting = status == "needs_you" and not run.get("ended_at") and run.get("need")
        if waiting:
            # The worker holds a thread while it waits on the person: answer once (then keep
            # polling), or let the run go so the next user's run isn't stuck behind it.
            if opts.answer and not answered:
                answered = True
                _call(client, result, "answer", "POST", f"/api/rsvp/{result.run_id}/answer",
                      json={"value": opts.answer})
            elif not opts.answer:
                cancelled = _call(client, result, "cancel", "POST",
                                  f"/api/rsvp/{result.run_id}/cancel")
                run = cancelled.json() if _ok(cancelled) else run
                break
        elif status in FINAL:
            break
        if time.monotonic() > deadline:
            result.errors.append(f"rsvp: still {status} after {opts.run_timeout:.0f}s")
            break
        sleep(opts.poll_every)
        polled = _call(client, result, "poll", "GET", f"/api/rsvp/{result.run_id}")
        if not _ok(polled):
            break
        run = polled.json()
    status = run.get("status")
    result.status = status if status in FINAL else "timeout"
    result.stop_reason = run.get("stop_reason")
    result.cost_usd = float(run.get("cost_usd") or 0.0)
    result.model = run.get("model")
    result.site = run.get("site")


def _forget_one(client, result: UserResult, into: str) -> None:
    memory = _call(client, result, "memory", "GET", "/api/memory")
    if not _ok(memory):
        return
    groups = memory.json()
    said = groups.get("said") or []
    facts = said + (groups.get("went") or []) + (groups.get("skipped") or [])
    if not facts:
        return
    mine = [f for f in said if into.lower()[:12] in (f.get("text") or "").lower()]
    fact = (mine or said or facts)[-1]
    forgot = _call(client, result, "forget", "DELETE", f"/api/memory/{fact['id']}")
    result.forgot = _ok(forgot)


# ---------------------------------------------------------------- summary


def summarize(results: list[UserResult]) -> list[dict]:
    rows = []
    groups = [(arm, [r for r in results if r.arm == arm]) for arm in ARMS]
    other = [r for r in results if r.arm not in ARMS]
    if other:
        groups.append(("?", other))
    groups.append(("all", results))
    for arm, rs in groups:
        runs = [r for r in rs if r.status is not None]
        hits = [r for r in rs if r.hit_rank]
        done = sum(r.status == "done" for r in runs)
        cost = sum(r.cost_usd for r in runs)
        rows.append({
            "arm": arm,
            "users": len(rs),
            "hit@k": round(len(hits) / len(rs), 2) if rs else 0.0,
            "mean_rank": round(sum(r.hit_rank for r in hits) / len(hits), 1) if hits else None,
            "opened": sum(bool(r.opened) for r in rs),
            "runs": len(runs),
            "done": done,
            "needs_you": sum(r.status == "needs_you" for r in runs),
            "failed": sum(r.status == "failed" for r in runs),
            "other": sum(r.status not in FINAL for r in runs),
            "rsvp_rate": round(done / len(rs), 2) if rs else 0.0,
            "cost_per_done": round(cost / done, 4) if done else None,
            "errors": sum(len(r.errors) for r in rs),
        })
    return rows


def rehearsals(results: list[UserResult]) -> list[dict]:
    """`Rehearsal` rows for the Ops screen (contracts.Rehearsal): runs that ended, per site.
    ok means none failed (handing back to the person is a fine ending)."""
    out = []
    sites = sorted({r.site or "luma" for r in results if r.status in FINAL})
    for site in sites:
        runs = [r for r in results if r.status in FINAL and (r.site or "luma") == site]
        done = sum(r.status == "done" for r in runs)
        failed = sum(r.status == "failed" for r in runs)
        out.append({"name": f"Eval replay, {site.title()}", "value": f"{done} of {len(runs)}",
                    "ok": failed == 0})
    return out


def format_table(rows: list[dict]) -> str:
    if not rows:
        return "(no users)"
    cols = list(rows[0])
    cells = [[("-" if row[c] is None else str(row[c])) for c in cols] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]
    line = lambda vals: "  ".join(v.rjust(w) if i else v.ljust(w)  # noqa: E731
                                  for i, (v, w) in enumerate(zip(vals, widths)))
    out = [line(cols), line(["-" * w for w in widths])]
    for i, r in enumerate(cells):
        if rows[i]["arm"] == "all":
            out.append(line(["-" * w for w in widths]))
        out.append(line(r))
    return "\n".join(out)


# ---------------------------------------------------------------- CLI


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--users", type=int, default=20, help="synthetic users (default 20)")
    p.add_argument("--base-url", default=os.getenv("EVAL_API_URL", "http://localhost:8000"))
    p.add_argument("--prefix", default="eval", help="user id prefix (default eval)")
    p.add_argument("--tag", default=None,
                   help="added to user ids; default is the time, so each run brings new users. "
                        "Pass --tag '' to reuse the same users.")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--top-k", type=int, default=5)
    p.add_argument("--always-rsvp", action="store_true",
                   help="RSVP to the opened pick even when it doesn't fit the persona")
    p.add_argument("--no-rsvp", action="store_true", help="skip RSVPs (feed and memory only)")
    p.add_argument("--no-extras", action="store_true", help="skip /api/taste, ask, week, activity")
    p.add_argument("--answer", default=None, help="code to answer 'needs you' prompts with")
    p.add_argument("--run-timeout", type=float, default=180.0)
    p.add_argument("--poll", type=float, default=1.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", dest="json_path", default=None, help="write per-user results here")
    p.add_argument("--rehearsals", nargs="?", const=REHEARSALS_PATH, default=None,
                   help=f"write Rehearsal rows for /api/ops (default path {REHEARSALS_PATH})")
    return p.parse_args(argv)


def user_ids(prefix: str, tag: str, n: int) -> list[str]:
    stem = f"{prefix}-{tag}" if tag else prefix
    return [f"{stem}-{i:03d}" for i in range(1, n + 1)]


def run_all(client: httpx.Client, ids: list[str], opts: Options, seed: int = 7,
            concurrency: int = 4, sleep=time.sleep) -> list[UserResult]:
    rng = random.Random(seed)
    personas = [PERSONAS[rng.randrange(len(PERSONAS))] for _ in ids]
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        return list(pool.map(lambda pair: run_user(client, pair[0], pair[1], opts, sleep),
                             zip(ids, personas)))


def main(argv: Optional[list[str]] = None, transport: Optional[httpx.BaseTransport] = None,
         sleep=time.sleep) -> list[dict]:
    args = parse_args(argv)
    tag = time.strftime("%m%d%H%M") if args.tag is None else args.tag
    ids = user_ids(args.prefix, tag, args.users)
    opts = Options(top_k=args.top_k, always_rsvp=args.always_rsvp, rsvp=not args.no_rsvp,
                   run_timeout=args.run_timeout, poll_every=args.poll, answer=args.answer,
                   extras=not args.no_extras)
    with httpx.Client(base_url=args.base_url, timeout=30.0, transport=transport) as client:
        if transport is None:
            try:
                client.get("/api/health").raise_for_status()
            except httpx.HTTPError as exc:
                sys.exit(f"API not reachable at {args.base_url}: {exc}")
        print(f"Replaying the demo for {len(ids)} users against {args.base_url} "
              f"({ids[0]} .. {ids[-1]})", flush=True)
        results = run_all(client, ids, opts, seed=args.seed, concurrency=args.concurrency,
                          sleep=sleep)
    rows = summarize(results)
    print(format_table(rows))
    skipped = sorted({s for r in results for s in r.skipped})
    if skipped:
        print("Not mounted yet, skipped: " + ", ".join(skipped))
    errors = [(r.user_id, e) for r in results for e in r.errors]
    for user_id, error in errors[:10]:
        print(f"  {user_id}: {error}")
    if len(errors) > 10:
        print(f"  ... and {len(errors) - 10} more errors")
    if args.json_path:
        with open(args.json_path, "w") as f:
            json.dump({"summary": rows, "users": [asdict(r) for r in results]}, f, indent=2)
    if args.rehearsals:
        with open(args.rehearsals, "w") as f:
            json.dump(rehearsals(results), f, indent=2)
        print(f"Wrote {args.rehearsals}")
    return rows


if __name__ == "__main__":
    main()
