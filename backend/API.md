# Event Agent API

Owned by the lead. Part 5 implements it; Part 7 (web) calls it. All JSON shapes are the
Pydantic models in `app/contracts.py`. Every route is under `/api` (the web app proxies `/api`
to `http://localhost:8000`). One demo user (`demo`) for now: no auth.

| Method and path | Body | Returns | Screen |
| --- | --- | --- | --- |
| GET /api/health | | `{ok: true}` | |
| GET /api/me | | `User` | all |
| POST /api/verify/start | | `VerifySession` | Verify |
| GET /api/verify/status | | `{verified: bool}` (also updates the stored user) | Verify |
| POST /api/verify/webhook | Persona webhook body | `{ok: true}` | |
| GET /api/feed | | `FeedResponse` (`picks`, `into` chips, `arm`) | For you |
| POST /api/into | `{text: string}` | `FeedResponse` after saving it to memory | For you |
| DELETE /api/into/{tag} | | `FeedResponse` without that chip | For you |
| GET /api/events/{id} | | `Event` | RSVP progress |
| POST /api/rsvp | `{event_id: string}` | `RsvpRun` (status `queued`; 403 if the user isn't verified) | RSVP progress |
| GET /api/rsvp/{run_id} | | `RsvpRun` with all five `steps` | RSVP progress (poll every 1 s) |
| GET /api/rsvp/{run_id}/frame | | `image/png` of hosted Chrome, 404 if none yet | Watch the agent |
| GET /api/memory | | `FactGroups` | Memory |
| DELETE /api/memory/{fact_id} | | `FactGroups` | Memory |
| DELETE /api/memory | | `FactGroups` (empty) | Memory, Forget everything |

## Routes for the updated canvas (added 12:50 PM)

Owner in brackets: Part 5 adds its routes to `main.py`; Part 8 serves the rest from an
`APIRouter` in `app/extra/router.py`, which Part 5 mounts with one `include_router` line.

| Method and path | Body | Returns | Screen | Owner |
| --- | --- | --- | --- | --- |
| POST /api/auth/start | `{email}` | `{sent: true, dev_code?: string}` (dev_code only when `AUTH_DEV_CODES=1`) | Welcome | 8 |
| POST /api/auth/verify | `{email, code}` | `User`; sets cookie `ea_user` | Welcome | 8 |
| POST /api/auth/logout | | `{ok: true}` | Profile | 8 |
| PATCH /api/me | any of `{spend_limit_usd, ask_before, usual_times, name}` | `User` | Profile, Taste | 5 |
| DELETE /api/me | | `{ok: true}` (forgets memory too) | Profile | 5 |
| GET /api/sites | | `SiteConnection[]` (Luma, Partiful, Meetup, Eventbrite) | Connect, Sites tab | 8 |
| POST /api/sites/{site}/connect | | `SiteConnection` | Connect | 8 |
| DELETE /api/sites/{site} | | `SiteConnection` | Connect | 8 |
| GET /api/calendar | | `{connected: bool, source: string}` | Connect | 8 |
| POST /api/calendar/connect | | `{connected: bool, source: string}` | Connect | 8 |
| POST /api/taste | `TasteRequest` | `FeedResponse` | Taste | 8 |
| GET /api/week | | `WeekResponse` | Week | 8 |
| POST /api/ask | `{text}` | `AskResponse` | Ask | 8 |
| GET /api/events/{id}/detail | | `EventDetail` | Event sheet | 8 |
| POST /api/rsvp/{run_id}/answer | `{value}` (the code) | `RsvpRun` | Needs you | 5 |
| POST /api/rsvp/{run_id}/cancel | | `RsvpRun` | Needs you | 5 |
| GET /api/activity | | `ActivityResponse` | What the agent did | 8 |
| GET /api/ops | | `OpsSummary` | Team dashboard (`/ops`) | 8 |

Current user, in order: cookie `ea_user`, header `X-User-Id`, else the demo user.

Needs you: when the agent calls `ask_user(need)`, the worker saves `need` on the run, sets status
`needs_you`, and blocks the agent's thread until `POST /api/rsvp/{id}/answer` (or cancel, or
`need.expires_at`, 10 minutes). Then status goes back to `running` and the answer returns to the agent.
A run that ends handing back (`RsvpResult.status == "needs_you"`) keeps its `need` for the screen.

Spend limit: before starting a run, the worker sums today's `cost_usd` for the user; at or over
`spend_limit_usd`, the run stops at once with a `spend_limit` need ("The agent stops and asks before
going over"). When it ends `done`, the worker also calls `Calendar.add_event` and remembers a `went`-style
fact through memory, for the You're going checklist.

## RSVP runs

RSVP runs: `POST /api/rsvp` stores the run with all five steps `waiting`, queues it in the worker,
and returns at once. The worker calls `RsvpAgent.run(...)`, saving each step and the latest frame as
they arrive. Final status: `done` ("You're going"), `needs_you`, or `failed`, with `stop_reason`.

Amplitude events (sent through `Telemetry.track`): `verify_started`, `verify_passed`, `pick_shown`,
`pick_opened`, `into_added`, `rsvp_started`, `rsvp_step`, `rsvp_done`, `rsvp_needs_you`,
`memory_forgotten`.
