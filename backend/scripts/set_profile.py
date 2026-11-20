"""Tell Event Agent who you are, so the RSVP agent can fill registration forms for you.

Saves each field as a memory in Cognee (through the running backend), shown on the Memory
screen under "You said" with the source "Your profile". Run it with the backend up:

    cd backend
    .venv/bin/python scripts/set_profile.py --github https://github.com/you \
        --linkedin https://linkedin.com/in/you --website https://you.dev \
        --title "ML Engineer" --company "Acme" --school "UC San Diego" --location "San Francisco" \
        --bio "I build personal agents and evals."
    .venv/bin/python scripts/set_profile.py --show
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

FIELDS = ["github", "linkedin", "website", "title", "company", "school", "location", "phone", "bio"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for field in FIELDS:
        parser.add_argument(f"--{field}")
    parser.add_argument("--show", action="store_true", help="print the saved profile")
    parser.add_argument("--api", default=os.getenv("EVAL_API_URL", "http://localhost:8000"))
    args = parser.parse_args()

    body = {f: getattr(args, f) for f in FIELDS if getattr(args, f) is not None}
    try:
        if body:
            resp = httpx.put(f"{args.api}/api/profile", json=body, timeout=120)
        else:
            resp = httpx.get(f"{args.api}/api/profile", timeout=30)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Couldn't reach the backend at {args.api}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(resp.json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
