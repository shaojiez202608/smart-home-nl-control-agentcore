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
import logging
import secrets
import time
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from .ambiguity import resolve_ambiguity
from .catalog import capability_summary
from .memory_policy import alias_record, preferences_from_actions
from .messages import FALLBACK_REPLY, control_reply, describe_action
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
    ) -> None:
        self.cache = cache
        self.directory = directory
        self.agent = agent
        self.dispatcher = dispatcher
        self.preferences = preferences
        self.metrics = metrics or NullMetrics()
        self.max_workers = max_workers

    # ------------------------------------------------------------------ public

    def handle(
        self,
        user_id: str,
        utterance: str,
        channel: str = "app",
        idempotency_key: str | None = None,
    ) -> OrchestratorResponse:
        t0 = time.perf_counter()
        utterance = (utterance or "").strip()[:MAX_UTTERANCE_CHARS]
        request_id = uuidlib.uuid4().hex

        if idempotency_key:
            cached = self.cache.get_json(self._idem_key(user_id, idempotency_key))
            if cached:
                self.metrics.put("IdempotentReplay", 1, "Count")
                return OrchestratorResponse(**cached)

        if not utterance:
            return OrchestratorResponse(request_id, "chat", "请告诉我你想做什么。")

        # 1. Preload device context (cache first, platform API on miss).
        t = time.perf_counter()
        whitelist = self._load_devices(user_id)
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
                )
            )
            plan = Plan.from_json(raw)
        except PlanParseError as exc:
            timings["inference"] = _ms(t)
            # Raw model output goes to troubleshooting logs only.
            log.warning(
                "plan_parse_failed",
                extra={"request_id": request_id, "error": str(exc), "raw": raw[:2000]},
            )
            self.metrics.put("PlanParseFailure", 1, "Count")
            return self._finish(
                OrchestratorResponse(request_id, "error", FALLBACK_REPLY), t0, timings
            )
        timings["inference"] = _ms(t)

        if plan.type == "control":
            resp = self._control(request_id, user_id, utterance, channel, plan, whitelist, timings)
        elif plan.type == "alias" and plan.alias:
            resp = self._alias(request_id, user_id, plan, whitelist)
        else:
            reply = plan.message.strip()[:MAX_REPLY_CHARS] or FALLBACK_REPLY
            resp = OrchestratorResponse(request_id, plan.type, reply)

        resp = self._finish(resp, t0, timings)
        if idempotency_key:
            self.cache.set_json(
                self._idem_key(user_id, idempotency_key), resp.to_dict(), IDEMPOTENCY_TTL
            )
        return resp

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
        pending = self.cache.pop_json(f"confirm:{token}")  # single use
        if not pending or pending.get("user_id") != user_id:
            self.metrics.put("ConfirmationRejected", 1, "Count")
            return OrchestratorResponse(request_id, "error", "确认已过期或无效，请重新发起操作。")
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
    ) -> OrchestratorResponse:
        outcome = validate_plan(plan, whitelist)
        if outcome.rejected:
            log.warning(
                "plan_actions_rejected",
                extra={
                    "request_id": request_id,
                    "rejected": [asdict(r) for r in outcome.rejected],
                },
            )
            self.metrics.put("ActionsRejected", len(outcome.rejected), "Count")

        amb = resolve_ambiguity(outcome.actions, whitelist, utterance)
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
        self._write_preferences(user_id, accepted, whitelist, utterance, request_id)

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
            skipped=[
                {"uuid": s.uuid, "name": whitelist[s.uuid].name, "reason": s.reason}
                for s in outcome.skipped
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
        self, request_id: str, user_id: str, plan: Plan, whitelist: dict[str, Device]
    ) -> OrchestratorResponse:
        record = alias_record(plan.alias, whitelist)
        if record is None:
            return OrchestratorResponse(request_id, "clarify", "这个称呼没法对应到唯一的设备，能再说具体一点吗？")
        try:
            self.preferences.upsert(user_id, [record])
        except Exception:  # memory is auxiliary; never fail the request on it
            log.exception("alias_write_failed", extra={"request_id": request_id})
            return OrchestratorResponse(request_id, "error", "暂时没能记住这个称呼，请稍后再试。")
        device = whitelist[record.device_uuid]
        return OrchestratorResponse(
            request_id, "alias", f"好的，以后说“{record.value}”就是指{device.room}的{device.name}。"
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
        raw = None if force_refresh else self.cache.get_json(devices_key(user_id))
        if raw is None:
            self.metrics.put("DeviceCacheMiss", 1, "Count")
            devices = self.directory.list_user_devices(user_id)
            self.cache.set_json(
                devices_key(user_id), [d.to_dict() for d in devices], DEVICE_CACHE_TTL
            )
        else:
            self.metrics.put("DeviceCacheHit", 1, "Count")
            devices = [Device.from_dict(d) for d in raw]
        # Overlay the freshest reported state (written by the state-sync path).
        states = self.cache.mget_json([state_key(d.uuid) for d in devices]) if devices else []
        for d, s in zip(devices, states, strict=False):
            if s:
                d.state = {**d.state, **s}
        return {d.uuid: d for d in devices}

    def _session_id(self, user_id: str) -> str:
        key = f"session:{user_id}"
        sid = self.cache.get_json(key)
        if not sid:
            # AgentCore runtime session ids must be at least 33 characters.
            digest = hashlib.sha256(user_id.encode()).hexdigest()[:12]
            sid = f"{digest}-{uuidlib.uuid4().hex}"
        self.cache.set_json(key, sid, SESSION_IDLE_TTL)  # sliding idle timeout
        return sid

    def _write_preferences(
        self,
        user_id: str,
        accepted: list[PlannedAction],
        whitelist: dict[str, Device],
        utterance: str,
        request_id: str,
    ) -> None:
        records = preferences_from_actions(accepted, whitelist, utterance)
        if not records:
            return
        try:
            self.preferences.upsert(user_id, records)
            self.metrics.put("PreferencesWritten", len(records), "Count")
        except Exception:
            log.exception("preference_write_failed", extra={"request_id": request_id})

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
