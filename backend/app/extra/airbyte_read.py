"""Reads the `events` stream of Airbyte's Google Calendar source with PyAirbyte; prints JSON.

Runs in its own venv (PyAirbyte pins older sqlalchemy/structlog/pyarrow than the app uses), so it
imports nothing from `app`. AirbyteCalendar (calendar_airbyte.py) runs it as a subprocess:

    AIRBYTE_PYTHON -I app/extra/airbyte_read.py < config.json   # last line: EVENTS_JSON [...]

Config is the connector's own spec (checked against `source.config_spec`, Nov 20 2026):
    {"credentials": {"auth_type": "manual", "client_id": ..., "client_secret": ...,
                     "refresh_token": ...},   # refresh token needs calendar.readonly
     "calendarid": "primary"}                 # optional; empty = every calendar
Setup: uv venv --python 3.11 backend/.venv-airbyte && uv pip install --python backend/.venv-airbyte/bin/python airbyte
"""

from __future__ import annotations

import json
import os
import sys

MARKER = "EVENTS_JSON "
KEEP = ("id", "calendar_id", "summary", "start", "end", "status", "transparency", "recurrence",
        "recurringEventId", "originalStartTime")


def main() -> int:
    os.environ.setdefault("DO_NOT_TRACK", "1")  # PyAirbyte's anonymous usage reporting off
    config = json.load(sys.stdin)
    import airbyte as ab  # noqa: PLC0415  (only in the Airbyte venv)

    source = ab.get_source("source-google-calendar", config=config, source_manifest=True)
    source.check()
    out = []
    for record in source.get_records("events"):
        row = dict(record)
        out.append({k: row.get(k) for k in KEEP})
    # PyAirbyte prints its own lines to stdout too; ours is the one line after this marker.
    sys.stdout.write("\n" + MARKER + json.dumps(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
