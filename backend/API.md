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

RSVP runs: `POST /api/rsvp` stores the run with all five steps `waiting`, queues it in the worker,
and returns at once. The worker calls `RsvpAgent.run(...)`, saving each step and the latest frame as
they arrive. Final status: `done` ("You're going"), `needs_you`, or `failed`, with `stop_reason`.

Amplitude events (sent through `Telemetry.track`): `verify_started`, `verify_passed`, `pick_shown`,
`pick_opened`, `into_added`, `rsvp_started`, `rsvp_step`, `rsvp_done`, `rsvp_needs_you`,
`memory_forgotten`.
