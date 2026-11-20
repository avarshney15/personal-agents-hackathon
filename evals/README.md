# Evals

`run.py` replays the demo against the running API for N synthetic users, so the Amplitude
charts and the Statsig `ranking` experiment have data before the demo. It also works as a
ranking eval: each user has a hidden taste, and `hit@k` is the share of users whose top five
picks held an event that fits them.

Each user, sent as the `X-User-Id` header:

1. `GET /api/me`, `POST /api/verify/start`, `GET /api/verify/status` (the fake Persona passes)
2. `GET /api/feed`, then `POST /api/into` with the persona's "into right now" text
3. `POST /api/taste`, `POST /api/ask`, `GET /api/week`, `GET /api/activity` when they're mounted
4. Opens the first of the top five picks that fits (`GET /api/events/{id}`), or the top pick
5. RSVPs to it if it fits (`POST /api/rsvp`) and polls `GET /api/rsvp/{id}` to the end. A run
   that stops to ask the person is cancelled (or answered with `--answer CODE`) so it doesn't
   hold a worker thread.
6. Forgets the fact it added (`DELETE /api/memory/{id}`)

## Run it

```
cd backend
.venv/bin/uvicorn app.main:app --port 8000          # in another terminal
.venv/bin/python ../evals/run.py --users 20
```

Options: `--users 20`, `--concurrency 4`, `--always-rsvp` (RSVP even when the pick doesn't fit,
fills the funnel faster), `--no-rsvp`, `--no-extras`, `--answer 123456`, `--tag ''` (reuse the
same user ids; by default each run gets new ones), `--json out.json`, `--rehearsals` (writes
`evals/rehearsals.json` for the Ops screen: per site, runs done of runs ended, ok when none failed). Base URL: `EVAL_API_URL`
(default `http://localhost:8000`).

To send real data, start the API with `SPONSORS_FAKE=` set so telemetry is live (for example
`SPONSORS_FAKE=memory,identity,ranking`) and `AMPLITUDE_API_KEY` and `STATSIG_SERVER_SECRET` in
`backend/.env`. With `ACT_FAKE=1`, set `FAKE_STEP_SECONDS=0.2` to make the runs quick.

Example output, one row per arm (illustrative numbers):

```
arm    users  hit@k  mean_rank  opened  runs  done  needs_you  failed  other  rsvp_rate  cost_per_done  errors
tfidf     10    0.5        2.4      10     5     4          1       0      0        0.4           0.031       0
sie       10    0.8        1.3      10     8     7          1       0      0        0.7           0.029       0
all       20   0.65        1.7      20    13    11          2       0      0       0.55            0.03       0
```

Tests (mocked HTTP): `cd backend && .venv/bin/python -m pytest -q tests/test_telemetry.py`.

## Dashboards

Amplitude (project "Event Agent", key in `AMPLITUDE_API_KEY`):

- Agent runs over time: Segmentation, event `rsvp_started`, totals, hourly, grouped by user
  property `ranking_arm`. Add `[Agent] AI Response` as a second series for Claude calls.
- RSVP funnel: Funnel, `pick_opened` then `rsvp_started` then `rsvp_done`, grouped by
  `ranking_arm`.
- Pick opened by arm: Segmentation, event `pick_opened`, totals, grouped by `ranking_arm`.
- Agent Analytics shows each RSVP run as a session (session id = run id), with each Claude call's
  model, tokens, cost, latency and tool calls, and an `rsvp_success` score.

Statsig (server secret in `STATSIG_SERVER_SECRET`): experiment `ranking`, ID type userID, two
groups at 50/50 with a string parameter `arm`: Control `arm = "tfidf"`, Test `arm = "sie"`.
Start it; until it's started everyone gets the default, and the code falls back to its own hash
split. Metrics come from the same events, logged to Statsig as `rsvp_done`, `pick_opened` and so on.
