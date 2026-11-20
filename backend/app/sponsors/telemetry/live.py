"""Amplitude + Statsig telemetry for Event Agent (the "Measure" stage).

- wrap(): instruments the Anthropic client with Amplitude's agent analytics SDK
  (`amplitude-ai`). Every Claude call becomes an `[Agent] AI Response` event with model,
  tokens, cost, latency, stop reason and tool calls. `client.messages.create` goes through
  the SDK's own Anthropic wrapper; `client.beta.messages.create` (which that wrapper leaves
  alone) is traced here with `AmplitudeAI.track_ai_message`. If the AI SDK can't be used
  (not installed, or an AsyncAnthropic client), both `create` methods are wrapped by hand
  and send a plain `claude_call` event instead.
- track(): product events from backend/API.md, sent with the Amplitude Python SDK
  (`amplitude-analytics`) and mirrored to Statsig with `log_event` so the `ranking`
  experiment has metrics in Pulse.
- arm(): Statsig experiment `ranking` (Statsig Server Core SDK, `statsig-python-core`).
  Logs an exposure, caches the arm per user, and falls back to the deterministic hash in
  fake.py when Statsig can't answer.
- run(): optional context manager that groups the Claude calls of one RSVP run into one
  agent session (session id = run id) tagged with the user's arm.

Nothing here raises into callers: every failure is logged and skipped.
Env: AMPLITUDE_API_KEY, STATSIG_SERVER_SECRET; optional AMPLITUDE_SERVER_ZONE (US|EU),
AMPLITUDE_AI_CONTENT_MODE (full|metadata_only), STATSIG_ENVIRONMENT (leave unset = production).
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import functools
import inspect
import logging
import os
import threading
import time
import uuid
from datetime import date, datetime
from typing import Any, Iterator, Optional

from app.contracts import Arm
from app.sponsors.telemetry.fake import hash_arm

log = logging.getLogger("telemetry")

AGENT_ID = "event-agent"
AGENT_DESCRIPTION = "RSVPs for a verified person on the event site, from a hosted Chrome session"
SERVER_DEVICE_ID = "event-agent-server"  # used when a Claude call has no user (Amplitude needs one id)
EXPERIMENT = "ranking"
EXPERIMENT_PARAM = "arm"  # string parameter on the Statsig experiment: "tfidf" or "sie"
ARMS: tuple[Arm, ...] = ("tfidf", "sie")
CLAUDE_CALL_EVENT = "claude_call"  # fallback event when the AI SDK isn't usable
OUTCOME_SCORE = "rsvp_success"
OUTCOME_EVENTS = {"rsvp_done": 1.0, "rsvp_needs_you": 0.0, "rsvp_failed": 0.0}

_TRACED_FLAG = "_event_agent_traced"
_run_ctx: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("event_agent_run",
                                                                           default=None)


class LiveTelemetry:
    def __init__(self, amplitude_api_key: Optional[str] = None,
                 statsig_secret: Optional[str] = None, *,
                 amplitude: Any = None, ai: Any = None, statsig: Any = None,
                 statsig_wait_s: float = 3.0, register_atexit: bool = True) -> None:
        """Reads keys from the environment. `amplitude`, `ai` and `statsig` take ready-made
        clients instead (tests pass fakes)."""
        self._lock = threading.Lock()
        self._arms: dict[tuple[str, str], Arm] = {}
        self._closed = False
        self._statsig_wait_s = statsig_wait_s

        self._amp = amplitude if amplitude is not None else _make_amplitude(
            amplitude_api_key if amplitude_api_key is not None else os.getenv("AMPLITUDE_API_KEY"))
        self._ai = ai if ai is not None else _make_ai(self._amp)
        self._agent = None
        if self._ai is not None:
            try:
                self._agent = self._ai.agent(AGENT_ID, description=AGENT_DESCRIPTION)
            except Exception:
                log.warning("telemetry: amplitude-ai agent() failed", exc_info=True)

        self._statsig = statsig
        self._statsig_ready = threading.Event()
        if statsig is not None:
            self._statsig_ready.set()
        else:
            secret = statsig_secret if statsig_secret is not None else os.getenv(
                "STATSIG_SERVER_SECRET")
            if secret:
                threading.Thread(target=self._start_statsig, args=(secret,), daemon=True,
                                 name="statsig-init").start()
            else:
                log.warning("telemetry: STATSIG_SERVER_SECRET not set; arms use the hash split")
                self._statsig_ready.set()

        if register_atexit:
            atexit.register(self.close)

    # ------------------------------------------------------------ Telemetry protocol

    def wrap(self, client):
        """Returns `client` instrumented for agent analytics, same type and API."""
        if client is None or getattr(client, _TRACED_FLAG, False):
            return client
        try:
            wrapped = self._wrap_with_ai_sdk(client)
            if wrapped is None and self._amp is not None:
                wrapped = self._wrap_by_hand(client)
            if wrapped is None:
                return client
            with contextlib.suppress(Exception):
                setattr(wrapped, _TRACED_FLAG, True)
            return wrapped
        except Exception:
            log.warning("telemetry: wrap failed; Claude calls go untraced", exc_info=True)
            return client

    def track(self, user_id: str, event: str, props: Optional[dict] = None) -> None:
        try:
            clean = _clean(props)
            arm = self._arms.get((EXPERIMENT, user_id))
            if arm is not None:
                clean.setdefault("arm", arm)
        except Exception:
            log.warning("telemetry: bad props for %s", event, exc_info=True)
            clean = {}
        log.info("track %s %s %s", user_id, event, clean)
        self._send_amplitude(user_id, event, clean)
        self._send_statsig(user_id, event, clean)
        self._score_outcome(user_id, event, clean)

    def arm(self, user_id: str, experiment: str = EXPERIMENT) -> Arm:
        key = (experiment, user_id)
        cached = self._arms.get(key)
        if cached is not None:
            return cached
        arm: Optional[Arm] = None
        try:
            arm = self._statsig_arm(user_id, experiment)
        except Exception:
            log.warning("telemetry: Statsig get_experiment failed", exc_info=True)
        source = "statsig" if arm is not None else "hash"
        if arm is None:
            arm = hash_arm(user_id, experiment)
        with self._lock:
            first = key not in self._arms
            arm = self._arms.setdefault(key, arm)
        if first:
            self._set_user_props(user_id, {f"{experiment}_arm": arm,
                                           f"{experiment}_arm_source": source})
        return arm

    def flush(self) -> None:
        try:
            futures = (self._ai.flush() if self._ai is not None
                       else self._amp.flush() if self._amp is not None else [])
            for future in futures or []:
                if future is not None:
                    with contextlib.suppress(Exception):
                        future.result(timeout=5)
        except Exception:
            log.warning("telemetry: Amplitude flush failed", exc_info=True)
        try:
            if self._statsig is not None:
                _wait(self._statsig.flush_events(), 5)
        except Exception:
            log.warning("telemetry: Statsig flush failed", exc_info=True)

    # ------------------------------------------------------------ extras (not on the protocol)

    @contextlib.contextmanager
    def run(self, user_id: str, run_id: str, arm: Optional[Arm] = None,
            event_id: Optional[str] = None) -> Iterator[Any]:
        """Groups the Claude calls inside the block into one agent session for this RSVP run
        (session id = run_id), tagged with the user's ranking arm. Use it in the worker thread:

            with telemetry.run(user.id, run.id, event_id=event.id):
                result = agent.run(...)
        """
        arm = arm or self._arms.get((EXPERIMENT, user_id))
        token = _run_ctx.set({"user_id": user_id, "run_id": run_id, "arm": arm,
                              "event_id": event_id})
        session = None
        try:
            if self._agent is not None:
                experiments = ([{"key": EXPERIMENT, "variant": arm, "provider": "statsig"}]
                               if arm else None)
                candidate = self._agent.session(
                    session_id=run_id, user_id=user_id, experiments=experiments,
                    context={"event_id": event_id} if event_id else None)
                candidate.__enter__()
                session = candidate
        except Exception:
            log.warning("telemetry: could not open the agent session", exc_info=True)
        try:
            yield session
        finally:
            if session is not None:
                try:
                    session.__exit__(None, None, None)
                except Exception:
                    log.warning("telemetry: could not close the agent session", exc_info=True)
            _run_ctx.reset(token)

    def close(self) -> None:
        """Flushes everything and shuts Statsig down. Registered with atexit."""
        if self._closed:
            return
        self._closed = True
        self.flush()
        try:
            if self._statsig is not None:
                _wait(self._statsig.shutdown(), 3)
        except Exception:
            log.debug("telemetry: Statsig shutdown failed", exc_info=True)

    # ------------------------------------------------------------ Statsig

    def _start_statsig(self, secret: str) -> None:
        try:
            from statsig_python_core import Statsig, StatsigOptions

            options = StatsigOptions(init_timeout_ms=3000, output_log_level="warn")
            env = os.getenv("STATSIG_ENVIRONMENT")
            if env:
                options.environment = env
            statsig = Statsig(secret, options)
            statsig.initialize().wait()
            details = statsig.get_initialize_details()
            if not details.is_config_spec_ready:
                reason = details.failure_details.reason if details.failure_details else details.source
                log.warning("telemetry: Statsig has no config yet (%s); arms use the hash split",
                            reason)
            self._statsig = statsig
        except Exception:
            log.warning("telemetry: Statsig failed to start; arms use the hash split",
                        exc_info=True)
        finally:
            self._statsig_ready.set()

    def _statsig_arm(self, user_id: str, experiment: str) -> Optional[Arm]:
        if not self._statsig_ready.wait(self._statsig_wait_s) or self._statsig is None:
            return None
        exp = self._statsig.get_experiment(_statsig_user(user_id), experiment)  # logs exposure
        value = exp.get_string(EXPERIMENT_PARAM, "")
        if isinstance(value, str) and value.strip().lower() in ARMS:
            return value.strip().lower()  # type: ignore[return-value]
        return _arm_from_group(getattr(exp, "group_name", None))

    def _send_statsig(self, user_id: str, event: str, props: dict) -> None:
        if self._statsig is None:
            return
        try:
            value = props.get("event_id")
            metadata = {k: v if isinstance(v, str) else _json_str(v)
                        for k, v in props.items() if v is not None}
            self._statsig.log_event(_statsig_user(user_id), event,
                                    value if isinstance(value, str) else None, metadata)
        except Exception:
            log.warning("telemetry: Statsig log_event %s failed", event, exc_info=True)

    # ------------------------------------------------------------ Amplitude

    def _send_amplitude(self, user_id: str, event: str, props: dict) -> None:
        if self._amp is None:
            return
        try:
            from amplitude import BaseEvent

            ids = {"user_id": user_id} if user_id else {"device_id": SERVER_DEVICE_ID}
            self._amp.track(BaseEvent(event_type=event, event_properties=props, **ids))
        except Exception:
            log.warning("telemetry: Amplitude track %s failed", event, exc_info=True)

    def _set_user_props(self, user_id: str, props: dict) -> None:
        if self._amp is None or not user_id:
            return
        try:
            from amplitude import EventOptions, Identify

            identify = Identify()
            for key, value in props.items():
                identify.set(key, value)
            self._amp.identify(identify, EventOptions(user_id=user_id))
        except Exception:
            log.warning("telemetry: Amplitude identify failed", exc_info=True)

    def _score_outcome(self, user_id: str, event: str, props: dict) -> None:
        """RSVP outcome as an `[Agent] Score` on the run's agent session."""
        run_id = props.get("run_id")
        if self._ai is None or event not in OUTCOME_EVENTS or not run_id:
            return
        try:
            value = OUTCOME_EVENTS[event]
            if props.get("status") in ("needs_you", "failed"):
                value = 0.0
            self._ai.score(user_id=user_id or None,
                           device_id=None if user_id else SERVER_DEVICE_ID,
                           name=OUTCOME_SCORE, value=value, target_id=str(run_id),
                           target_type="session", source="ai", session_id=str(run_id),
                           agent_id=AGENT_ID, comment=props.get("stop_reason") or None)
        except Exception:
            log.warning("telemetry: amplitude-ai score failed", exc_info=True)

    # ------------------------------------------------------------ Claude tracing

    def _wrap_with_ai_sdk(self, client):
        """Amplitude's own Anthropic wrapper (a subclass of anthropic.Anthropic)."""
        if self._ai is None:
            return None
        try:
            import anthropic
            from amplitude_ai import wrap as ai_wrap
        except Exception:
            log.warning("telemetry: amplitude-ai unavailable; tracing Claude by hand",
                        exc_info=True)
            return None
        if not isinstance(client, anthropic.Anthropic):
            return None  # AsyncAnthropic etc.: the AI SDK has no wrapper yet
        try:
            wrapped = ai_wrap(client, amplitude=self._ai, device_id=SERVER_DEVICE_ID)
        except Exception:
            log.warning("telemetry: amplitude_ai.wrap failed; tracing Claude by hand",
                        exc_info=True)
            return None
        try:
            beta_messages = wrapped.beta.messages
            beta_messages.create = self._traced(beta_messages.create, "beta.messages",
                                                self._trace_ai_response)
        except Exception:
            log.warning("telemetry: could not trace beta.messages", exc_info=True)
        return wrapped

    def _wrap_by_hand(self, client):
        """Fallback: patch messages.create and beta.messages.create on the client itself."""
        patched = False
        for api, resource in (("messages", _attr(client, "messages")),
                              ("beta.messages", _attr(_attr(client, "beta"), "messages"))):
            create = _attr(resource, "create")
            if create is None:
                continue
            try:
                resource.create = self._traced(create, api, self._trace_claude_call)
                patched = True
            except Exception:
                log.warning("telemetry: could not trace %s", api, exc_info=True)
        return client if patched else None

    def _traced(self, create, api: str, emit):
        """Wraps a create method (sync or async). Tracing never changes what the call returns
        or raises."""

        def record(kwargs: dict, response: Any, started: float, error: Optional[BaseException]):
            if kwargs.get("stream"):
                return  # streamed responses aren't traced on this path
            try:
                emit(api, kwargs, response, (time.perf_counter() - started) * 1000, error)
            except Exception:
                log.warning("telemetry: Claude trace failed", exc_info=True)

        async def finish(pending, kwargs: dict, started: float):
            try:
                response = await pending
            except Exception as exc:
                record(kwargs, None, started, exc)
                raise
            record(kwargs, response, started, None)
            return response

        # AsyncAnthropic's create is a plain function that returns a coroutine, so detect
        # the awaitable result instead of the function type.
        @functools.wraps(create)
        def traced(*args, **kwargs):
            started = time.perf_counter()
            try:
                response = create(*args, **kwargs)
            except Exception as exc:
                record(kwargs, None, started, exc)
                raise
            if inspect.isawaitable(response):
                return finish(response, kwargs, started)
            record(kwargs, response, started, None)
            return response
        return traced

    def _trace_ai_response(self, api: str, kwargs: dict, response: Any, latency_ms: float,
                           error: Optional[BaseException]) -> None:
        """`[Agent] AI Response` through amplitude-ai, for calls its wrapper doesn't see."""
        from amplitude_ai import get_active_context

        ctx = get_active_context()
        run = _run_ctx.get() or {}
        user_id = (ctx.user_id if ctx else None) or run.get("user_id")
        session_id = (ctx.session_id if ctx else None) or run.get("run_id") or str(uuid.uuid4())
        usage = getattr(response, "usage", None)
        self._ai.track_ai_message(
            user_id=user_id, device_id=None if user_id else SERVER_DEVICE_ID,
            session_id=session_id, trace_id=ctx.trace_id if ctx else None, agent_id=AGENT_ID,
            content=_response_text(response),
            model=getattr(response, "model", None) or kwargs.get("model") or "unknown",
            provider="anthropic", latency_ms=latency_ms,
            input_tokens=_int(usage, "input_tokens"), output_tokens=_int(usage, "output_tokens"),
            cache_read_tokens=_int(usage, "cache_read_input_tokens"),
            cache_creation_tokens=_int(usage, "cache_creation_input_tokens"),
            finish_reason=getattr(response, "stop_reason", None),
            tool_calls=_tool_calls(response),
            max_output_tokens=kwargs.get("max_tokens"),
            is_error=error is not None,
            error_message=str(error) if error is not None else None,
            error_type=type(error).__name__ if error is not None else None,
            event_properties={"api": api, **_run_props(run)},
        )

    def _trace_claude_call(self, api: str, kwargs: dict, response: Any, latency_ms: float,
                           error: Optional[BaseException]) -> None:
        """Plain `claude_call` event through the Amplitude SDK (fallback path)."""
        run = _run_ctx.get() or {}
        usage = getattr(response, "usage", None)
        tools = [c["function"]["name"] for c in _tool_calls(response) or []]
        props = {
            "api": api,
            "model": getattr(response, "model", None) or kwargs.get("model") or "unknown",
            "input_tokens": _int(usage, "input_tokens"),
            "output_tokens": _int(usage, "output_tokens"),
            "cache_read_input_tokens": _int(usage, "cache_read_input_tokens"),
            "latency_ms": round(latency_ms, 1),
            "stop_reason": getattr(response, "stop_reason", None),
            "tool_calls": tools,
            "tool_call_count": len(tools),
            "is_error": error is not None,
            "error_type": type(error).__name__ if error is not None else None,
            **_run_props(run),
        }
        self._send_amplitude(run.get("user_id") or "", CLAUDE_CALL_EVENT,
                             {k: v for k, v in props.items() if v is not None})


# ---------------------------------------------------------------- helpers


def _make_amplitude(api_key: Optional[str]):
    if not api_key:
        log.warning("telemetry: AMPLITUDE_API_KEY not set; events are only logged")
        return None
    try:
        from amplitude import Amplitude, Config

        # min_id_length=1: the demo user id is "demo", shorter than Amplitude's default of 5.
        config = Config(min_id_length=1,
                        server_zone=os.getenv("AMPLITUDE_SERVER_ZONE", "US").upper())
        return Amplitude(api_key, config)
    except Exception:
        log.warning("telemetry: Amplitude failed to start; events are only logged",
                    exc_info=True)
        return None


def _make_ai(amplitude):
    if amplitude is None:
        return None
    try:
        from amplitude_ai import AIConfig, AmplitudeAI, ContentMode

        mode = ContentMode(os.getenv("AMPLITUDE_AI_CONTENT_MODE", "full").strip().lower())
        return AmplitudeAI(amplitude=amplitude, config=AIConfig(content_mode=mode))
    except Exception:
        log.warning("telemetry: amplitude-ai unavailable; Claude calls go out as %s",
                    CLAUDE_CALL_EVENT, exc_info=True)
        return None


def _statsig_user(user_id: str):
    from statsig_python_core import StatsigUser

    return StatsigUser(user_id)


def _arm_from_group(group_name: Optional[str]) -> Optional[Arm]:
    name = (group_name or "").strip().lower()
    if not name:
        return None
    if "sie" in name or "superlinked" in name:
        return "sie"
    if "tfidf" in name or "tf-idf" in name or "control" in name:
        return "tfidf"
    if "test" in name or "treatment" in name:
        return "sie"
    return None


def _wait(handle: Any, timeout: float) -> None:
    if handle is not None and hasattr(handle, "wait"):
        handle.wait(timeout)


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def _int(obj: Any, name: str) -> Optional[int]:
    value = getattr(obj, name, None) if obj is not None else None
    return value if isinstance(value, int) else None


def _response_text(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def _tool_calls(response: Any) -> Optional[list[dict]]:
    calls = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) in ("tool_use", "server_tool_use"):
            calls.append({"type": "function", "id": getattr(block, "id", ""),
                          "function": {"name": getattr(block, "name", ""),
                                       "arguments": _json_str(getattr(block, "input", {}))}})
    return calls or None


def _run_props(run: dict) -> dict:
    return {k: run[k] for k in ("run_id", "arm", "event_id") if run.get(k)}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        with contextlib.suppress(Exception):
            return _jsonable(value.model_dump(mode="json"))
    return str(value)


def _clean(props: Optional[dict]) -> dict:
    return {str(k): _jsonable(v) for k, v in (props or {}).items()}


def _json_str(value: Any) -> str:
    import json

    try:
        return json.dumps(_jsonable(value), separators=(",", ":"))
    except Exception:
        return str(value)
