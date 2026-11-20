"""Listens to the telemetry the other parts already send, for the Activity and Ops screens.

Wraps the shared Telemetry object's `track` (same object the registry hands everyone) so that,
besides going to Amplitude/Statsig as before, two kinds of event are also counted here:
  - `pick_shown` (or `feed_shown`/`feed_viewed`): For you picks shown per ranking arm, the
    denominator of the Ops dashboard's RSVP rate per arm;
  - `rsvp_needs_you` with `waiting: true`: the agent asked the person for something mid-run
    (a code), shown as "Asked you for a code · Answered" on Activity.
The wrap never changes what is sent and never raises.
"""

from __future__ import annotations

import logging
from typing import Optional

from app import registry
from app.extra import data
from app.extra.store_extra import get_extra_store

log = logging.getLogger("extra.taps")

VIEW_EVENTS = {"pick_shown", "feed_shown", "feed_viewed", "feed_view"}


def _count(props: dict) -> int:
    for key in ("count", "picks_count", "n"):
        if isinstance(props.get(key), int):
            return props[key]
    for key in ("event_ids", "picks", "events"):
        if isinstance(props.get(key), (list, tuple)):
            return len(props[key])
    return 1


def observe(telemetry, user_id: str, event: str, props: Optional[dict]) -> None:
    props = props or {}
    if event in VIEW_EVENTS:
        arm = props.get("arm")
        if arm not in ("sie", "tfidf"):
            try:
                arm = telemetry.arm(user_id)
            except Exception:
                arm = None
        if arm:
            get_extra_store().add_picks_shown(arm, _count(props), data.today_start().date().isoformat())
    elif event == "rsvp_needs_you" and props.get("waiting") and props.get("run_id") and props.get("kind"):
        get_extra_store().add_ask(str(props["run_id"]), str(props["kind"]), user_id)


def install() -> bool:
    """Idempotent. True when the shared telemetry object is now tapped."""
    try:
        telemetry = registry.get_telemetry()
    except Exception:
        log.exception("no telemetry to tap")
        return False
    if getattr(telemetry, "_extra_tapped", False):
        return True
    original = telemetry.track

    def track(user_id: str, event: str, props: Optional[dict] = None) -> None:
        try:
            observe(telemetry, user_id, event, props)
        except Exception:
            log.exception("observing %s failed", event)
        return original(user_id, event, props)

    try:
        telemetry.track = track
        telemetry._extra_tapped = True
    except Exception:  # a slotted or frozen object: Ops falls back to sample numbers
        log.exception("couldn't tap telemetry")
        return False
    return True
