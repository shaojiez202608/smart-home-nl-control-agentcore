"""AgentCore Runtime entrypoint.

Payload (from the orchestrator Lambda):
    {"user_id", "session_id", "utterance", "channel", "devices": [...]}

The device context arrives preloaded, so the common control path needs a
single model call. The agent reads remembered preferences and recent turns
from AgentCore Memory, and reaches the existing platform API only through
AgentCore Gateway, authenticating with an M2M token obtained from AgentCore
Identity.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

import boto3
from bedrock_agentcore.identity import requires_access_token
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.tools.mcp import MCPClient

from .planner import run_plan

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("agent")

REGION = os.environ.get("AWS_REGION", "us-east-1")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
GATEWAY_TARGET = os.environ.get("GATEWAY_TARGET_NAME", "platform")
OAUTH_PROVIDER = os.environ.get("OAUTH_PROVIDER_NAME", "")
OAUTH_SCOPE = os.environ.get("OAUTH_SCOPE", "")
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "6"))

app = BedrockAgentCoreApp()
memory = boto3.client("bedrock-agentcore", region_name=REGION)


def preferences_namespace(user_id: str) -> str:
    return f"/members/{user_id}/preferences/"


def read_preferences(user_id: str) -> list[str]:
    if not MEMORY_ID:
        return []
    try:
        resp = memory.list_memory_records(
            memoryId=MEMORY_ID, namespace=preferences_namespace(user_id), maxResults=50
        )
        return [r["content"]["text"] for r in resp.get("memoryRecordSummaries", [])]
    except Exception:
        log.exception("read_preferences_failed")
        return []


def read_history(user_id: str, session_id: str) -> list[dict[str, str]]:
    if not MEMORY_ID:
        return []
    try:
        resp = memory.list_events(
            memoryId=MEMORY_ID,
            actorId=user_id,
            sessionId=session_id,
            includePayloads=True,
            maxResults=HISTORY_TURNS * 2,
        )
    except Exception:
        log.exception("read_history_failed")
        return []
    turns = []
    for ev in sorted(resp.get("events", []), key=lambda e: e["eventTimestamp"]):
        for p in ev.get("payload", []):
            conv = p.get("conversational")
            if conv:
                turns.append({"role": conv["role"].lower(), "text": conv["content"]["text"][:300]})
    return turns[-HISTORY_TURNS * 2 :]


def write_turn(user_id: str, session_id: str, utterance: str, plan: dict[str, Any]) -> None:
    """Short-term memory of the conversation (for follow-ups like "再暗一点")."""
    if not MEMORY_ID:
        return
    summary = json.dumps(
        {"type": plan["type"], "actions": [[a["uuid"], a["action"], a["params"]] for a in plan["actions"]]},
        ensure_ascii=False,
    )
    try:
        memory.create_event(
            memoryId=MEMORY_ID,
            actorId=user_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(UTC),
            payload=[
                {"conversational": {"role": "USER", "content": {"text": utterance}}},
                {"conversational": {"role": "ASSISTANT", "content": {"text": summary}}},
            ],
        )
    except Exception:
        log.exception("write_turn_failed")


@requires_access_token(provider_name=OAUTH_PROVIDER or "unset", scopes=[OAUTH_SCOPE], auth_flow="M2M")
def _gateway_token(*, access_token: str) -> str:
    return access_token


def gateway_status_lookup(user_id: str):
    tool_name = f"{GATEWAY_TARGET}___queryDeviceStatus"

    def lookup(uuid: str) -> dict[str, Any]:
        token = _gateway_token()
        client = MCPClient(url=GATEWAY_URL, headers={"Authorization": f"Bearer {token}"})
        with client:
            # user_id is injected by code, never chosen by the model.
            result = client.call_tool_sync(
                tool_use_id=f"status-{uuid}", name=tool_name, arguments={"uuid": uuid, "userId": user_id}
            )
        texts = [c.get("text", "") for c in result.get("content", [])]
        return {"status": result.get("status"), "result": "".join(texts)[:2000]}

    return lookup


@app.entrypoint
def invoke(payload: dict[str, Any], context: Any = None) -> dict[str, Any]:
    user_id = str(payload["user_id"])
    session_id = str(payload.get("session_id") or getattr(context, "session_id", "") or "default-session")
    utterance = str(payload.get("utterance", ""))[:500]
    out = run_plan(
        utterance=utterance,
        devices=list(payload.get("devices") or []),
        preferences=read_preferences(user_id),
        history=read_history(user_id, session_id),
        channel=str(payload.get("channel", "app")),
        status_lookup=gateway_status_lookup(user_id),
    )
    # Persist the turn off the critical path.
    threading.Thread(
        target=write_turn, args=(user_id, session_id, utterance, out["plan"]), daemon=True
    ).start()
    log.info(
        json.dumps(
            {"event": "plan_ready", "type": out["plan"]["type"], "usage": out["usage"], "tool_calls": out["tool_calls"]}
        )
    )
    return out


if __name__ == "__main__":
    app.run()
