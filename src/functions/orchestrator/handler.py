"""Orchestrator Lambda: the session-management step of the request path.

Entry points:
* API Gateway (REST, Amazon Cognito authorizer) - the mobile app channel:
    POST /v1/commands            {"utterance": "...", "idempotencyKey": "..."}
    GET  /v1/devices             preloaded device context (debug/demo)
    GET  /v1/confirmations       sensitive actions waiting for confirmation
    POST /v1/confirmations       {"token": "..."}
    GET  /v1/commands/{id}       execution receipt from the control platform
* AWS IoT rule - the voice-speaker channel. Speakers publish recognised text
  to ``<prefix>/speakers/<speakerId>/utterance``; the reply is published to
  ``<prefix>/speakers/<speakerId>/reply``. The speaker's owner is looked up on
  the platform; confirmations are never accepted on this channel.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from smarthome_core.messages import UNAVAILABLE_REPLY
from smarthome_core.orchestrator import IdempotencyConflict, Orchestrator

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("orchestrator")

TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")


class _Deps:
    """Clients are built on first use, so the module imports without AWS (tests inject fakes)."""

    def __init__(self) -> None:
        self.metrics: Any = None
        self.platform: Any = None
        self.orch: Orchestrator | None = None
        self.iot: Any = None

    def ready(self) -> _Deps:
        if self.orch is None:
            from smarthome_aws.agent_client import RuntimeAgentClient
            from smarthome_aws.cache import ValkeyCache
            from smarthome_aws.memory_store import AgentCoreMemoryStore
            from smarthome_aws.metrics import EmfMetrics
            from smarthome_aws.platform_client import PlatformClient

            self.metrics = EmfMetrics()
            self.platform = PlatformClient()
            self.orch = Orchestrator(
                cache=ValkeyCache.from_env(),
                directory=self.platform,
                agent=RuntimeAgentClient(self.metrics),
                dispatcher=self.platform,
                preferences=AgentCoreMemoryStore(),
                metrics=self.metrics,
                session_idle_seconds=int(os.environ.get("SESSION_IDLE_SECONDS", "900")),
            )
            self.iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))
        return self


deps = _Deps()
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    try:  # build clients during Lambda init (faster first request); failures retry on first use
        deps.ready()
    except Exception:
        log.exception("init_failed")


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


# Routes whose body is a user-facing reply: a downstream failure still answers 200 with the fixed
# fallback text (DECISIONS.md D27). Read-only routes answer 503 with a fixed error body instead.
REPLY_ROUTES = {("POST", "/v1/commands"), ("POST", "/v1/confirmations")}


def _api(event: dict) -> dict:
    method, resource = event.get("httpMethod", ""), event.get("resource", "")
    try:
        return _route(event, method, resource)
    except IdempotencyConflict:
        return _resp(409, {"error": "idempotency key already used for a different request"})
    except Exception as exc:  # never let a downstream failure become an unhandled 5xx
        log.exception(
            "handler_failed %s", json.dumps({"route": f"{method} {resource}", "error": type(exc).__name__})
        )
        _count("HandlerFailure")
        if (method, resource) in REPLY_ROUTES:
            return _resp(200, {"type": "error", "message": UNAVAILABLE_REPLY, "actions": [], "skipped": []})
        return _resp(503, {"error": "temporarily unavailable"})


def _count(name: str) -> None:
    try:
        if deps.metrics is not None:
            deps.metrics.put(name, 1, "Count")
            deps.metrics.flush({"PlanType": "error"})
    except Exception:  # metrics must never mask the original failure
        log.warning("metric_emit_failed")


def _route(event: dict, method: str, resource: str) -> dict:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    user_id = claims.get("sub")
    if not user_id:  # the authorizer should already have rejected this
        return _resp(401, {"error": "unauthenticated"})
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid json"})
    if not isinstance(body, dict):
        return _resp(400, {"error": "invalid json"})
    d = deps.ready()

    if method == "POST" and resource == "/v1/commands":
        utterance = body.get("utterance")
        if not isinstance(utterance, str) or not utterance.strip():
            return _resp(400, {"error": "utterance required"})
        key = body.get("idempotencyKey") or (event.get("headers") or {}).get("Idempotency-Key")
        key = key if isinstance(key, str) and key else None
        return _resp(200, d.orch.handle(user_id, utterance, "app", key).to_dict())
    if method == "GET" and resource == "/v1/devices":
        devices = d.orch._load_devices(user_id)  # noqa: SLF001 - read-only demo endpoint
        return _resp(200, {"devices": [x.to_dict() for x in devices.values()]})
    if method == "GET" and resource == "/v1/confirmations":
        return _resp(200, {"pending": d.orch.pending_confirmations(user_id)})
    if method == "POST" and resource == "/v1/confirmations":
        token = body.get("token")
        if not isinstance(token, str) or not token:
            return _resp(400, {"error": "token required"})
        return _resp(200, d.orch.confirm(user_id, token).to_dict())
    if method == "GET" and resource == "/v1/commands/{commandId}":
        status, data = d.platform.command_receipt(user_id, event["pathParameters"]["commandId"])
        return _resp(status, data)
    return _resp(404, {"error": "not found"})


def _speaker(event: dict) -> None:
    speaker_id = str(event.get("speakerId", ""))
    utterance = str(event.get("utterance", ""))
    d = deps.ready()
    try:
        owner = d.platform.speaker_owner(speaker_id) if speaker_id else None
        if not owner:
            log.warning("unknown_speaker %s", json.dumps({"speakerId": speaker_id[:64]}))
            return
        result = d.orch.handle(owner, utterance, "speaker").to_dict()
    except Exception as exc:
        log.exception("speaker_failed %s", json.dumps({"error": type(exc).__name__}))
        _count("HandlerFailure")
        result = {"type": "error", "message": UNAVAILABLE_REPLY}
    result.pop("pending_confirmation", None)  # tokens never leave on the voice channel
    d.iot.publish(
        topic=f"{TOPIC_PREFIX}/speakers/{speaker_id}/reply",
        qos=1,
        payload=json.dumps(result, ensure_ascii=False).encode(),
    )


def handler(event: dict, context: Any) -> dict | None:
    if "httpMethod" in event:
        return _api(event)
    if event.get("source") == "speaker":
        _speaker(event)
        return None
    log.warning("unrecognised_event")
    return None
