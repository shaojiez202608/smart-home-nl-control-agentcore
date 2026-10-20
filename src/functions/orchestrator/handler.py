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
from smarthome_aws.agent_client import RuntimeAgentClient
from smarthome_aws.cache import ValkeyCache
from smarthome_aws.memory_store import AgentCoreMemoryStore
from smarthome_aws.metrics import EmfMetrics
from smarthome_aws.platform_client import PlatformClient
from smarthome_core.orchestrator import Orchestrator

logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("orchestrator")

TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")

_metrics = EmfMetrics()
_platform = PlatformClient()
_orch = Orchestrator(
    cache=ValkeyCache.from_env(),
    directory=_platform,
    agent=RuntimeAgentClient(_metrics),
    dispatcher=_platform,
    preferences=AgentCoreMemoryStore(),
    metrics=_metrics,
    session_idle_seconds=int(os.environ.get("SESSION_IDLE_SECONDS", "900")),
)
_iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _api(event: dict) -> dict:
    claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims") or {}
    user_id = claims.get("sub")
    if not user_id:  # the authorizer should already have rejected this
        return _resp(401, {"error": "unauthenticated"})
    method, resource = event["httpMethod"], event["resource"]
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid json"})

    if method == "POST" and resource == "/v1/commands":
        utterance = body.get("utterance")
        if not isinstance(utterance, str) or not utterance.strip():
            return _resp(400, {"error": "utterance required"})
        key = body.get("idempotencyKey") or (event.get("headers") or {}).get("Idempotency-Key")
        return _resp(200, _orch.handle(user_id, utterance, "app", key).to_dict())
    if method == "GET" and resource == "/v1/devices":
        devices = _orch._load_devices(user_id)  # noqa: SLF001 - read-only demo endpoint
        return _resp(200, {"devices": [d.to_dict() for d in devices.values()]})
    if method == "GET" and resource == "/v1/confirmations":
        return _resp(200, {"pending": _orch.pending_confirmations(user_id)})
    if method == "POST" and resource == "/v1/confirmations":
        token = body.get("token")
        if not isinstance(token, str) or not token:
            return _resp(400, {"error": "token required"})
        return _resp(200, _orch.confirm(user_id, token).to_dict())
    if method == "GET" and resource == "/v1/commands/{commandId}":
        status, data = _platform.command_receipt(user_id, event["pathParameters"]["commandId"])
        return _resp(status, data)
    return _resp(404, {"error": "not found"})


def _speaker(event: dict) -> None:
    speaker_id = str(event.get("speakerId", ""))
    utterance = str(event.get("utterance", ""))
    owner = _platform.speaker_owner(speaker_id) if speaker_id else None
    if not owner:
        log.warning("unknown_speaker", extra={"speakerId": speaker_id})
        return
    result = _orch.handle(owner, utterance, "speaker").to_dict()
    result.pop("pending_confirmation", None)  # tokens never leave on the voice channel
    _iot.publish(
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
