# Setup: every sponsor track, working for real

Everything below is built and merged on `main`, and it runs on fakes with no keys. This page lists what
each track needs to run for real, in the order to do it. Put every key in `backend/.env` (copy
`.env.example`, which lists every variable the code reads).

| Track | State today | What you need | Time |
| --- | --- | --- | --- |
| Claude RSVP agent (every demo beat needs it) | Built; steps 1–2 checked on real Luma, steps 3–5 never run with a real model | Anthropic key, one Luma sign-in, one test RSVP | 15 min |
| Persona | Built, tested with mocked HTTP | Sandbox key, template id, `SPONSORS_FAKE` change, a copy decision | 15 min |
| Cognee | Works for real now, keyless | Nothing; Cognee Cloud optional | 0–10 min |
| Superlinked SIE | Built, tested with mocked HTTP | SIE key | 10 min |
| Amplitude + Statsig | Built, tested with mocked SDKs | Two keys, an experiment, four charts, an eval run | 30 min |
| Airbyte (stretch) | Built, never run against Google | Separate venv, Google OAuth refresh token | 30 min |
| Arcee (stretch) | Seam only: the client isn't written | Key, then about 30 min of code | 45 min |
| SingleStore (stretch) | Not built | Account, then about an hour of code | 60+ min |
| Firedrill | Not connected; Ops shows our own eval replay instead | Account with event code TOKENSAND, its CLI | unknown |
| Guild AI | Not connected; the spend cap is our own code | Access, sales-gated | unknown |
| Senso | Not targeted | — | — |

## 0. Base setup (once)

```
cp .env.example backend/.env
cd backend
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/uvicorn app.main:app --port 8000
cd ../web && npm install && npm run dev        # http://localhost:5173 (add ?mock=1 for canvas data)
```

`SPONSORS_FAKE` lists the sponsor modules that stay on fakes (`memory`, `ranking`, `identity`, `telemetry`);
`0` turns all four real. A module set real without its keys errors when it's used, so as you finish each
section below, remove that module from the list. `ACT_FAKE=0` turns on the real RSVP agent.

## 1. Claude RSVP agent (needed by every track's demo)

1. `ANTHROPIC_API_KEY=...` and `ACT_FAKE=0` in `backend/.env`. The same key also turns on Claude for
   tagging, "into right now" parsing and Ask (`TEXT_PROVIDER` blank).
2. For the stage, set `RSVP_MODEL=claude-opus-5-5`. The default, `claude-haiku-5-5`, costs less (the canvas
   shows Haiku at about $0.06 per RSVP) but is less reliable at multi-step screen work.
3. Sign in to Luma once in the agent's Chrome. The agent uses the real Chrome app, not Playwright's
   bundled browser, where Luma's "Verify you are human" check never clears:
   `cd backend && .venv/bin/python scripts/login_luma.py` opens Chrome on its own profile
   (`~/.event-agent/chrome`) at Luma's sign-in page. Tick the check yourself, sign in, and **leave that
   Chrome window open**: every RSVP runs in a new tab there, brought to the front, and the tab stays open
   on the result afterwards (`CHROME_KEEP_TABS=0` closes it).
   Check it: `.venv/bin/python scripts/login_luma.py --check`.
4. One real test RSVP (this registers your Luma account for a real event):
   `.venv/bin/python scripts/try_rsvp.py https://luma.com/fastmcp-meetup-dec26`
   It runs in a new tab of the agent's Chrome. Frames land in `backend/frames/try_rsvp/`. If Luma asks for
   an emailed code, the script prompts for it. If Luma shows a human check mid-run, the agent stops and hands
   back; tick it in that Chrome window and RSVP again.
5. Free events with no host questions in the catalog: `fastmcp-meetup-dec26` (plain Register, best),
   `mission-u38j`, `hz1hopu7` (host approval, so it reads back "Pending approval").

Known gap: Luma's form has no note field, and you chose not to message hosts, so on Luma the host is not
told an agent registered. Step 3 says so honestly. See Persona below for the copy that still promises it.

## 2. Persona: Best Use of Persona

1. At the Persona booth or dashboard, switch to **Sandbox**.
2. API → API Keys: copy the sandbox key (`persona_sandbox_...`) into `PERSONA_API_KEY`.
3. Inquiries → Templates: pick or create a published Government ID + Selfie template; copy its id
   (`itmpl_...`) into `PERSONA_INQUIRY_TEMPLATE_ID`.
4. Ask the booth whether `http://localhost:5173` is an allowed redirect. If the template or Domain Manager
   has an allowlist, add `localhost` (no `https://`). Redirect: `PERSONA_REDIRECT_URI`.
5. Keep the sandbox "Pass verifications" (Simulate) setting on, so any photo completes the inquiry.
6. Remove `identity` from `SPONSORS_FAKE`.
7. Optional: a webhook needs a public HTTPS tunnel to `/api/verify/webhook` (events `inquiry.completed`,
   `inquiry.approved`, `inquiry.failed`, `inquiry.declined`) and its secret in `PERSONA_WEBHOOK_SECRET`.
   Not needed: the Verify screen polls Persona every 2 s.
8. **Decide the copy.** The Verify screen still says the agent "tells the host it's an agent acting for you"
   and shows "What the host sees". On Luma that no longer happens. Either reword those lines (e.g. "Every RSVP
   is made for a verified person") or turn host messages back on. The Persona track's story rests on this.

## 3. Cognee: Best use of cognee

Works now with no key: set `COGNEE_KEYLESS=1` and remove `memory` from `SPONSORS_FAKE`. The local models
(~820 MB) are already downloaded on this machine; indexing the 5 demo facts takes about 45 s after start,
and recall answers from the keyword index until then.

Optional, for a stronger story at the Cognee booth: Cognee Cloud with `COGNEE_API_KEY` and
`COGNEE_SERVICE_URL` (your tenant URL), or local cognee with `LLM_API_KEY` (OpenAI by default).
Neither has run here.

## 4. Superlinked SIE: two tracks (Technical Build is our target)

1. Get an SIE Serverless key at https://superlinked.com/serverless ($20 free credits; ask the booth about
   hackathon credits). Put it in `SIE_API_KEY`; remove `ranking` from `SPONSORS_FAKE`.
2. Models default to `Qwen/Qwen3-Embedding-4B` and `Qwen/Qwen3-Reranker-4B`; confirm with the booth that your
   key can use them, or change `SIE_EMBED_MODEL` / `SIE_RERANK_MODEL`.
3. The 38 events are encoded once in the background at start and cached in `data/sie_cache.json`.
4. Only half the users see SIE (the Statsig experiment). For the demo user, check `/api/feed` returns
   `"arm": "sie"`; the For you footer says "You're in the new ranking this week" only for that arm.

## 5. Amplitude + Statsig: Best Use of Amplitude or Statsig

Amplitude:
1. Create a project "Event Agent"; copy its API key into `AMPLITUDE_API_KEY`.
2. Build one dashboard with: runs over time (`rsvp_started`, hourly, grouped by user property `ranking_arm`,
   plus `[Agent] AI Response`); the RSVP funnel `pick_opened` → `rsvp_started` → `rsvp_done` by
   `ranking_arm`; `pick_opened` by `ranking_arm`; optionally `[Agent] AI Response` cost and latency by model.
   Each RSVP run is one Agent Analytics session.

Statsig:
1. Copy the Server Secret Key into `STATSIG_SERVER_SECRET`. Leave `STATSIG_ENVIRONMENT` blank.
2. Create experiment `ranking`: ID type userID, 100% allocation, 50/50, string parameter `arm`:
   Control `tfidf`, Test `sie`. **Start it**, or everyone gets the default arm.
3. Metrics: `rsvp_done` (primary), `pick_opened`, `rsvp_started`, `rsvp_needs_you`. Results compute daily;
   on stage, show the Diagnostics tab.

Then remove `telemetry` from `SPONSORS_FAKE` and fill the charts before judging, with the server running:
`cd backend && .venv/bin/python ../evals/run.py --users 20 --rehearsals`
(keep `ACT_FAKE=1` for this run so 20 users don't make real Luma RSVPs). It also writes
`evals/rehearsals.json` for the Ops dashboard.

The Ops dashboard (`/ops`) shows sample numbers, labelled "Sample data", until each ranking arm has at
least 20 picks shown and `evals/rehearsals.json` exists.

## 6. Airbyte (stretch): Google Calendar clashes

1. `uv venv --python 3.11 backend/.venv-airbyte && uv pip install --python backend/.venv-airbyte/bin/python airbyte`
   (separate venv: PyAirbyte's pins would downgrade packages Cognee needs).
2. Google Cloud console: enable the Google Calendar API, create an OAuth client, and get a refresh token with
   scope `https://www.googleapis.com/auth/calendar.readonly` (the OAuth Playground works). Put them in
   `GOOGLE_CALENDAR_CLIENT_ID`, `GOOGLE_CALENDAR_CLIENT_SECRET`, `GOOGLE_CALENDAR_REFRESH_TOKEN`.
3. `CALENDAR_SOURCE=airbyte`. Users only see clashes after connecting a calendar (Connect screen).
4. Gap: it's read-only, but You're going always says "Added to your calendar". Change that row to "Added to
   your week" (`web/src/screens/Going.tsx`) before using Google Calendar on stage.

Without it, the demo calendar puts "Call with Sam" on the coming Tuesday, 6–7 pm.

## 7. Arcee (stretch): Most Impressive Use of the Pareto Frontier

Not working yet. `backend/app/catalog/text.py` routes tagging and intent to Arcee when
`TEXT_PROVIDER=arcee` and `ARCEE_API_KEY` are set, but `_arcee_tag_events` and `_arcee_read_taste` raise
`NotImplementedError`, so it falls back to keywords. To make it real: get a key and model name at
https://docs.arcee.ai, write those two functions against Arcee's API, and log tag agreement with Claude next
to cost per 1,000 events. That comparison is what the Pareto Frontier track asks for.

## 8. SingleStore (stretch): Best Use of SingleStoreDB

Not built. It would need a free Helios workspace (`SINGLESTORE_URL`), loading the catalog and its vectors,
and Ask answered by one hybrid SQL query.

## 9. Firedrill: Best Use of Firedrill

Not connected. It needs an account (event code `TOKENSAND`) and its npm CLI, and its docs describe no API.
The Ops "rehearsals" rows come from our own eval replay (`evals/run.py --rehearsals`), not Firedrill.

## 10. Guild AI: Most Interesting Use of Agents

Not connected (sales-gated). The $2-a-day spend cap and the activity log are our own code. The canvas's
plan was to run the sync scheduler under Guild with a per-person cap, if the booth gives access.

## Known limits to fix or disclose

- Sign-in codes are shown on screen (`AUTH_DEV_CODES=1`): there is no email provider.
- The `ea_user` cookie holds the raw user id, so it can be forged. Fine for a demo; sign it before real users.
- Only Luma is a real site. Connecting Partiful, Meetup or Eventbrite records the choice only.
- There's no climbing event in SF in this window. "AI agents and hackathons" shows the reorder best.
- Pushing to GitHub doesn't need anything new: the repo is public at
  https://github.com/avarshney15/personal-agents-hackathon.

## Submission (by 4:30 PM PT, https://tokensand.com/personalagenthack/submit)

- [x] Public GitHub repo
- [ ] Short demo video with a shareable link (record a full run as a backup, too)
- [ ] What you built and the tools you used (README + this page)
- [ ] Team names and contact emails
