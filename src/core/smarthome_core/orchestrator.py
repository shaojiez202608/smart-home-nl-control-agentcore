"""Request orchestration: preload -> single inference -> validate -> dispatch.

This is the code path a user waits on. Its acceptance-latency target
(measured from request receipt to the acceptance response, excluding
physical execution on the device) shapes the design:

* Device context is **preloaded** from the cache before the model is
  called, so the model does not spend a round trip on a "list devices" tool
  call. This is a request-orchestration optimisation and works the same
  with or without a managed agent runtime.
* Validated actions are dispatched to the device control service **in
  parallel**, and the response returns as soon as the control service has
  *accepted* them. Device execution is reported asynchronously.
* The reply text is generated here from dispatch results, never taken from
  the model for control requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from .ambiguity import resolve_ambiguity
from .catalog import capability_summary, filter_reported_state
from .memory_policy import (
    PreferenceRecord,
    alias_record,
    display_name,
    memory_aliases,
    preferences_from_actions,
    refreshes_for_used_preferences,
)
from .messages import FALLBACK_REPLY, UNAVAILABLE_REPLY, control_reply, describe_action
from .models import Device, DispatchResult, Plan, PlannedAction, PlanParseError
from .policy import apply_policy
from .ports import (
    AgentClient,
    AgentRequest,
    Cache,
    CommandDispatcher,
    DeviceDirectory,
    Metrics,
    NullMetrics,
    PreferenceStore,
)
from .validation import validate_plan

log = logging.getLogger(__name__)

DEVICE_CACHE_TTL = 300
SESSION_IDLE_TTL = 900
CONFIRM_TTL = 120
IDEMPOTENCY_TTL = 60
MAX_UTTERANCE_CHARS = 500
MAX_REPLY_CHARS = 300


def devices_key(user_id: str) -> str:
    return f"ctx:devices:{user_id}"


def state_key(uuid: str) -> str:
    return f"state:{uuid}"


def prefs_key(user_id: str) -> str:
    return f"prefs:{user_id}"


PREFS_CACHE_TTL = 3600
IN_PROGRESS_REPLY = "相同的请求正在处理中，请稍后再试。"


class IdempotencyConflict(Exception):
    """The idempotency key was already used for a different request (HTTP 409)."""


@dataclass
class OrchestratorResponse:
    request_id: str
    type: str
    message: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    pending_confirmation: dict[str, Any] | None = None
    skipped: list[dict[str, Any]] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Orchestrator:
    def __init__(
        self,
        *,
        cache: Cache,
        directory: DeviceDirectory,
        agent: AgentClient,
        dispatcher: CommandDispatcher,
        preferences: PreferenceStore,
        metrics: Metrics | None = None,
        max_workers: int = 8,
        session_idle_seconds: int = SESSION_IDLE_TTL,
    ) -> None:
        self.cache = cache
        self.directory = directory
        self.agent = agent
        self.dispatcher = dispatcher
        self.preferences = preferences
        self.metrics = metrics or NullMetrics()
        self.max_workers = max_workers
        self.session_idle_seconds = session_idle_seconds

    # ------------------------------------------------------------------ public

    def handle(
        self,
        user_id: str,
        utterance: str,
        channel: str = "app",
        idempotency_key: str | None = None,
    ) -> OrchestratorResponse:
        """Never raises for downstream failures: every failure becomes the fixed fallback reply.

        The one exception is ``IdempotencyConflict`` (a client error, mapped to HTTP 409).
        """
        t0 = time.perf_counter()
        utterance = (utterance or "").strip()[:MAX_UTTERANCE_CHARS]
        request_id = uuidlib.uuid4().hex
        idem = self._idem_key(user_id, idempotency_key) if idempotency_key else None
        fingerprint = hashlib.sha256(f"{channel}\n{utterance}".encode()).hexdigest()
        claimed = False
        try:
            if idem:
                replay = self._claim(idem, fingerprint)
                if replay is not None:
                    self.metrics.flush({"PlanType": replay.type})
                    return replay
                claimed = True
            resp = self._handle(request_id, user_id, utterance, channel, t0)
            if claimed:
                if resp.type == "error":
                    self.cache.pop_json(idem)  # let the client retry a failed request
                else:
                    self.cache.set_json(
                        idem, {"fp": fingerprint, "state": "done", "resp": resp.to_dict()}, IDEMPOTENCY_TTL
                    )
            return resp
        except IdempotencyConflict:
            self.metrics.put("IdempotencyConflict", 1, "Count")
            self.metrics.flush({"PlanType": "error"})
            raise
        except Exception as exc:  # downstream failure outside the guarded steps: fallback, never a 5xx
            log.exception(
                "request_failed %s", json.dumps({"request_id": request_id, "error": type(exc).__name__})
            )
            if claimed:
                try:
                    self.cache.pop_json(idem)
                except Exception:  # the placeholder expires on its own
                    log.warning("idempotency_release_failed")
            return self._fail(request_id, "UnhandledFailure", t0, t0, UNAVAILABLE_REPLY)

    def _claim(self, idem: str, fingerprint: str) -> OrchestratorResponse | None:
        """Reserve an idempotency key (SET NX). Returns a stored response to replay, if any."""
        if self.cache.add_json(idem, {"fp": fingerprint, "state": "pending"}, IDEMPOTENCY_TTL):
            return None
        held = self.cache.get_json(idem) or {}
        if held.get("fp") != fingerprint:
            raise IdempotencyConflict("idempotency key reused for a different request")
        self.metrics.put("IdempotentReplay", 1, "Count")
        if held.get("state") == "done" and isinstance(held.get("resp"), dict):
            return OrchestratorResponse(**held["resp"])
        return OrchestratorResponse(uuidlib.uuid4().hex, "error", IN_PROGRESS_REPLY)

    def _handle(
        self, request_id: str, user_id: str, utterance: str, channel: str, t0: float
    ) -> OrchestratorResponse:
        if not utterance:
            return self._finish(OrchestratorResponse(request_id, "chat", "请告诉我你想做什么。"), t0, {})

        # 1. Preload device context (cache first, platform API on miss).
        t = time.perf_counter()
        try:
            whitelist = self._load_devices(user_id)
        except Exception as exc:
            log.exception(
                "preload_failed %s", json.dumps({"request_id": request_id, "error": type(exc).__name__})
            )
            return self._fail(request_id, "PreloadFailure", t, t0, UNAVAILABLE_REPLY)
        records = self._load_preferences(user_id, whitelist)
        timings = {"preload": _ms(t)}

        # 2. Single inference on the managed agent runtime.
        t = time.perf_counter()
        raw = ""
        try:
            raw = self.agent.plan(
                AgentRequest(
                    user_id=user_id,
                    session_id=self._session_id(user_id),
                    utterance=utterance,
                    channel=channel,
                    devices=[_agent_view(d) for d in whitelist.values()],
                    preferences=[r.text for r in records],
                )
            )
            plan = Plan.from_json(raw)
        except PlanParseError as exc:
            # Raw model output goes to troubleshooting logs only.
            log.warning(
                "plan_parse_failed %s",
                json.dumps(
                    {"request_id": request_id, "error": str(exc), "raw": raw[:2000]}, ensure_ascii=False
                ),
                extra={"request_id": request_id, "error": str(exc), "raw": raw[:2000]},
            )
            return self._fail(request_id, "PlanParseFailure", t, t0)
        except Exception as exc:  # runtime or model failure: answer with the fallback, never a 5xx
            log.exception(
                "agent_invocation_failed %s",
                json.dumps({"request_id": request_id, "error": type(exc).__name__}),
            )
            return self._fail(request_id, "AgentInvocationFailure", t, t0)
        timings["inference"] = _ms(t)

        aliases = memory_aliases(records, whitelist)
        if plan.type == "control":
            resp = self._control(
                request_id, user_id, utterance, channel, plan, whitelist, timings, records, aliases
            )
        elif plan.type == "alias" and plan.alias:
            resp = self._alias(request_id, user_id, plan, whitelist, aliases)
        else:
            reply = plan.message.strip()[:MAX_REPLY_CHARS] or FALLBACK_REPLY
            resp = OrchestratorResponse(request_id, plan.type, reply)
        return self._finish(resp, t0, timings)

    def pending_confirmations(self, user_id: str) -> list[dict[str, Any]]:
        """Held sensitive actions awaiting confirmation (e.g. requested by voice)."""
        items = self.cache.get_json(f"pending:{user_id}") or []
        alive = self.cache.mget_json([f"confirm:{i['token']}" for i in items]) if items else []
        return [i for i, a in zip(items, alive, strict=False) if a]

    def confirm(self, user_id: str, token: str) -> OrchestratorResponse:
        """Execute actions previously held for explicit user confirmation.

        Must only be exposed on an authenticated user channel (the app API),
        never on the voice channel.
        """
        t0 = time.perf_counter()
        request_id = uuidlib.uuid4().hex
        try:
            pending = self.cache.pop_json(f"confirm:{token}")  # single use
            if not pending or pending.get("user_id") != user_id:
                self.metrics.put("ConfirmationRejected", 1, "Count")
                return self._finish(
                    OrchestratorResponse(request_id, "error", "确认已过期或无效，请重新发起操作。"), t0, {}
                )
            # Re-check against a fresh whitelist: permissions may have changed.
            whitelist = self._load_devices(user_id, force_refresh=True)
            plan = Plan.from_dict({"type": "control", "actions": pending["actions"]})
            outcome = validate_plan(plan, whitelist)
            results = self._dispatch_all(user_id, outcome.actions, request_id)
            message = control_reply(whitelist, results, [], [], [], len(outcome.rejected), [])
            resp = OrchestratorResponse(
                request_id, "control", message, actions=_result_view(results, whitelist)
            )
            return self._finish(resp, t0, {})
        except Exception as exc:
            log.exception(
                "confirm_failed %s", json.dumps({"request_id": request_id, "error": type(exc).__name__})
            )
            return self._fail(request_id, "ConfirmFailure", t0, t0, UNAVAILABLE_REPLY)

    # ----------------------------------------------------------------- control

    def _control(
        self,
        request_id: str,
        user_id: str,
        utterance: str,
        channel: str,
        plan: Plan,
        whitelist: dict[str, Device],
        timings: dict[str, float],
        records: list[PreferenceRecord],
        aliases: dict[str, set[str]],
    ) -> OrchestratorResponse:
        outcome = validate_plan(plan, whitelist)
        if outcome.rejected:
            rejected = [asdict(r) for r in outcome.rejected]
            log.warning(
                "plan_actions_rejected %s",
                json.dumps({"request_id": request_id, "rejected": rejected}, ensure_ascii=False),
                extra={"request_id": request_id, "rejected": rejected},
            )
            self.metrics.put("ActionsRejected", len(outcome.rejected), "Count")

        amb = resolve_ambiguity(outcome.actions, whitelist, utterance, aliases)
        if amb.groups:
            self.metrics.put("AmbiguousReferences", len(amb.groups), "Count")
        policy = apply_policy(amb.actions, whitelist)
        if policy.needs_confirmation or policy.denied:
            self.metrics.put(
                "SensitiveActionsHeld",
                len(policy.needs_confirmation) + len(policy.denied),
                "Count",
            )

        t = time.perf_counter()
        results = self._dispatch_all(user_id, policy.allowed, request_id)
        timings["dispatch"] = _ms(t)

        pending = None
        if policy.needs_confirmation:
            pending = self._hold_for_confirmation(user_id, policy.needs_confirmation, whitelist, channel)

        accepted = [
            PlannedAction(r.uuid, r.action, r.params, _source(outcome.actions, r))
            for r in results
            if r.accepted
        ]
        self._write_preferences(user_id, accepted, whitelist, utterance, request_id, records)

        message = control_reply(
            whitelist,
            results,
            policy.needs_confirmation,
            policy.denied,
            outcome.skipped,
            len(outcome.rejected),
            outcome.conflicts,
            amb.question(),
        )
        only_question = bool(amb.groups) and not (results or pending)
        return OrchestratorResponse(
            request_id,
            "clarify" if only_question else "control",
            message,
            actions=_result_view(results, whitelist),
            pending_confirmation=pending,
            # Reason codes only (models.SKIP_REASONS); never model-written text.
            skipped=[
                {"uuid": s.uuid, "name": whitelist[s.uuid].name, "reason": s.reason} for s in outcome.skipped
            ],
        )

    def _hold_for_confirmation(
        self,
        user_id: str,
        actions: list[PlannedAction],
        whitelist: dict[str, Device],
        channel: str,
    ) -> dict[str, Any]:
        token = secrets.token_urlsafe(18)
        self.cache.set_json(
            f"confirm:{token}",
            {"user_id": user_id, "actions": [asdict(a) for a in actions]},
            CONFIRM_TTL,
        )
        pending = {
            "token": token,
            "expires_in": CONFIRM_TTL,
            "requested_via": channel,
            "actions": [
                {
                    "uuid": a.uuid,
                    "name": whitelist[a.uuid].name,
                    "action": a.action,
                    "description": describe_action(whitelist[a.uuid], a),
                }
                for a in actions
            ],
        }
        listing = [p for p in self.pending_confirmations(user_id)][-9:] + [pending]
        self.cache.set_json(f"pending:{user_id}", listing, CONFIRM_TTL)
        return pending

    def _alias(
        self,
        request_id: str,
        user_id: str,
        plan: Plan,
        whitelist: dict[str, Device],
        aliases: dict[str, set[str]],
    ) -> OrchestratorResponse:
        decision = alias_record(plan.alias, whitelist, aliases)
        record = decision.record
        if decision.conflict_with is not None:
            self.metrics.put("AliasConflict", 1, "Count")
            return OrchestratorResponse(
                request_id,
                "clarify",
                f"“{plan.alias.alias.strip()}”已经用来称呼{display_name(decision.conflict_with)}，请换一个称呼。",
            )
        if record is None:
            return OrchestratorResponse(
                request_id, "clarify", "这个称呼没法对应到唯一的设备，能再说具体一点吗？"
            )
        try:
            self.preferences.upsert(user_id, [record])
            self.cache.pop_json(prefs_key(user_id))
        except Exception:  # memory is auxiliary; never fail the request on it
            log.exception("alias_write_failed", extra={"request_id": request_id})
            return OrchestratorResponse(request_id, "error", "暂时没能记住这个称呼，请稍后再试。")
        device = whitelist[record.device_uuid]
        return OrchestratorResponse(
            request_id, "alias", f"好的，以后说“{record.value}”就是指{display_name(device)}。"
        )

    # ----------------------------------------------------------------- helpers

    def _dispatch_all(
        self, user_id: str, actions: list[PlannedAction], request_id: str
    ) -> list[DispatchResult]:
        if not actions:
            return []

        def one(a: PlannedAction) -> DispatchResult:
            try:
                r = self.dispatcher.dispatch(user_id, a, request_id)
            except Exception as exc:  # network errors become failed results
                log.exception("dispatch_failed", extra={"request_id": request_id, "uuid": a.uuid})
                r = DispatchResult(a.uuid, a.action, False, error=type(exc).__name__)
            r.params = a.params
            return r

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(actions))) as pool:
            results = list(pool.map(one, actions))
        failed = sum(1 for r in results if not r.accepted)
        self.metrics.put("ActionsDispatched", len(results) - failed, "Count")
        if failed:
            self.metrics.put("DispatchFailures", failed, "Count")
        return results

    def _load_devices(self, user_id: str, force_refresh: bool = False) -> dict[str, Device]:
        """Device whitelist for this user. A cache failure falls back to the platform API;
        a platform failure raises (the caller turns it into the fallback reply)."""
        raw = None
        if not force_refresh:
            try:
                raw = self.cache.get_json(devices_key(user_id))
            except Exception:
                log.warning("device_cache_read_failed", exc_info=True)
                self.metrics.put("CacheFailure", 1, "Count")
        if raw is None:
            self.metrics.put("DeviceCacheMiss", 1, "Count")
            devices = self.directory.list_user_devices(user_id)
            self._cache_quietly(devices_key(user_id), [d.to_dict() for d in devices], DEVICE_CACHE_TTL)
        else:
            self.metrics.put("DeviceCacheHit", 1, "Count")
            devices = [Device.from_dict(d) for d in raw]
        # Overlay the freshest reported state (written by the state-sync path). Reported state is
        # device input: only catalog-defined keys and values are kept (it reaches the model prompt).
        try:
            states = self.cache.mget_json([state_key(d.uuid) for d in devices]) if devices else []
        except Exception:
            log.warning("state_cache_read_failed", exc_info=True)
            self.metrics.put("CacheFailure", 1, "Count")
            states = []
        for d, s in zip(devices, states, strict=False):
            if s:
                clean, _ = filter_reported_state(d.type, s)
                d.state = {**d.state, **clean}
        return {d.uuid: d for d in devices}

    def _load_preferences(self, user_id: str, whitelist: dict[str, Device]) -> list[PreferenceRecord]:
        """Remembered preferences and aliases for devices the user may still control (auxiliary:
        any failure returns an empty list)."""
        try:
            cached = self.cache.get_json(prefs_key(user_id))
        except Exception:
            log.warning("preference_cache_read_failed", exc_info=True)
            cached = None
        if cached is not None:
            records = [PreferenceRecord(**r) for r in cached if isinstance(r, dict)]
        else:
            try:
                records = self.preferences.list_records(user_id)
            except Exception:  # preferences are auxiliary; never fail the request on them
                log.exception("preference_read_failed")
                return []
            self._cache_quietly(prefs_key(user_id), [asdict(r) for r in records], PREFS_CACHE_TTL)
        return [r for r in records if r.device_uuid in whitelist]

    def _cache_quietly(self, key: str, value: Any, ttl: int) -> None:
        try:
            self.cache.set_json(key, value, ttl)
        except Exception:
            log.warning("cache_write_failed", exc_info=True)
            self.metrics.put("CacheFailure", 1, "Count")

    def _session_id(self, user_id: str) -> str:
        key = f"session:{user_id}"
        sid = self.cache.get_json(key)
        if not sid:
            # AgentCore runtime session ids must be at least 33 characters.
            digest = hashlib.sha256(user_id.encode()).hexdigest()[:12]
            sid = f"{digest}-{uuidlib.uuid4().hex}"
        self.cache.set_json(key, sid, self.session_idle_seconds)  # sliding idle timeout
        return sid

    def _write_preferences(
        self,
        user_id: str,
        accepted: list[PlannedAction],
        whitelist: dict[str, Device],
        utterance: str,
        request_id: str,
        known: list[PreferenceRecord],
    ) -> None:
        records = preferences_from_actions(accepted, whitelist, utterance)
        refresh = refreshes_for_used_preferences(accepted, known, datetime.now(UTC).isoformat())
        if not records and not refresh:
            return
        try:
            self.preferences.upsert(user_id, records + refresh)
            if records:
                self.metrics.put("PreferencesWritten", len(records), "Count")
            if refresh:
                self.metrics.put("PreferencesRefreshed", len(refresh), "Count")
        except Exception:
            log.exception("preference_write_failed", extra={"request_id": request_id})
        finally:
            try:
                self.cache.pop_json(prefs_key(user_id))  # next request re-reads the source of truth
            except Exception:
                log.warning("preference_cache_invalidate_failed", exc_info=True)

    def _fail(
        self, request_id: str, metric: str, t_step: float, t0: float, reply: str = FALLBACK_REPLY
    ) -> OrchestratorResponse:
        self.metrics.put(metric, 1, "Count")
        timings = {} if t_step == t0 else {"failedStep": _ms(t_step)}
        return self._finish(OrchestratorResponse(request_id, "error", reply), t0, timings)

    def _finish(
        self, resp: OrchestratorResponse, t0: float, timings: dict[str, float]
    ) -> OrchestratorResponse:
        timings["total"] = _ms(t0)
        resp.timings_ms = timings
        for k, v in timings.items():
            self.metrics.put(f"Latency{k.capitalize()}", v)
        self.metrics.flush({"PlanType": resp.type})
        return resp

    @staticmethod
    def _idem_key(user_id: str, key: str) -> str:
        # The stored entry also carries a fingerprint of the request, so a key reused for a
        # different utterance is detected instead of replaying the old response.
        return f"idem:{user_id}:{hashlib.sha256(key.encode()).hexdigest()[:32]}"


def _ms(t: float) -> float:
    return round((time.perf_counter() - t) * 1000, 1)


def _agent_view(d: Device) -> dict[str, Any]:
    return {
        "uuid": d.uuid,
        "name": d.name,
        "room": d.room,
        "type": d.type,
        "state": d.state,
        "aliases": d.aliases,
        "commands": capability_summary(d.type),
    }


def _source(actions: list[PlannedAction], r: DispatchResult) -> str:
    for a in actions:
        if a.uuid == r.uuid and a.action == r.action:
            return a.params_source
    return "default"


def _result_view(results: list[DispatchResult], whitelist: dict[str, Device]) -> list[dict]:
    return [
        {
            "uuid": r.uuid,
            "name": whitelist[r.uuid].name,
            "action": r.action,
            "params": r.params,
            "status": "accepted" if r.accepted else "failed",
            "command_id": r.command_id,
        }
        for r in results
    ]
