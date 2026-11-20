# Event Agent

An events agent that says who it is, remembers you, and proves it worked. Built at the
Personal Agents Hackathon (tokens&, San Francisco, November 20, 2026).

Event Agent finds events you'll like, then RSVPs for you on the event site itself from a Chrome
session it hosts for you. Every RSVP tells the host it's an agent acting for a verified person.

- Plan: [Core architecture](https://claude.ai/artifact/PZFsgYjuPDp36PsoPDuRda) ·
  [Agent architecture](https://claude.ai/artifact/6snCsmF9qDBbjUA8AaNENZ) ·
  [Design canvas](https://claude.ai/artifact/2HaekLGqUwmWq3kKQx3F61)
- Screens and design tokens from the canvas: `design/`
- Setup for every sponsor track: `SETUP.md`
- API: `backend/API.md` · Shared shapes and interfaces: `backend/app/contracts.py`

## Sponsors

| Stage | What happens | Built with |
| --- | --- | --- |
| Read | Your Luma events, plus a seeded list of real SF events | Our Luma reader |
| Remember | What you went to, skipped, and say you're into | Cognee |
| Rank | For you, each pick with a reason | Superlinked SIE (TF-IDF as the control arm) |
| Act | RSVP on the site itself, in hosted Chrome | Claude computer use |
| Prove | A verified person stands behind every RSVP | Persona |
| Measure | Every run traced; ranking run as an experiment | Amplitude + Statsig |

## Run it

```
cp .env.example backend/.env           # fill in keys; blank keys keep that sponsor on its fake
cd backend && uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/uvicorn app.main:app --reload --port 8000
cd ../web && npm install && npm run dev  # http://localhost:5173
```

Tests: `cd backend && .venv/bin/python -m pytest -q`
