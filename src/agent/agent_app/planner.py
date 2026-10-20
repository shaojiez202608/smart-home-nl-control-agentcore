"""Builds and runs the Strands agent for one request.

The agent is created per request. Tools that touch user data close over the
caller's ``user_id`` and device whitelist, so neither appears in a
model-facing parameter: a parameter the model can fill is a parameter a
prompt injection can fill.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from strands import Agent, tool
from strands.models import BedrockModel

from .prompts import build_prompt, build_system
from .schema import PlanModel

log = logging.getLogger(__name__)

StatusLookup = Callable[[str], dict[str, Any]]


def _model() -> BedrockModel:
    return BedrockModel(
        model_id=os.environ.get("MODEL_ID", "us.amazon.nova-lite-v1:0"),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        temperature=0.0,
        max_tokens=int(os.environ.get("MODEL_MAX_TOKENS", "1024")),
    )


def make_status_tool(whitelist: set[str], lookup: StatusLookup):
    @tool
    def query_device_status(uuid: str) -> str:
        """Fetch the real-time status of one device. Only use for explicit real-time status questions.

        Args:
            uuid: uuid of a device from the device list
        """
        if uuid not in whitelist:
            return json.dumps({"error": "unknown device"})
        try:
            return json.dumps(lookup(uuid), ensure_ascii=False)
        except Exception as exc:  # surface a readable failure to the model
            log.warning("status_lookup_failed", extra={"uuid": uuid, "error": str(exc)})
            return json.dumps({"error": "status unavailable"})

    return query_device_status


def run_plan(
    *,
    utterance: str,
    devices: list[dict[str, Any]],
    preferences: list[str],
    history: list[dict[str, str]],
    channel: str,
    status_lookup: StatusLookup,
    model: Any | None = None,
) -> dict[str, Any]:
    """Return {"plan": <dict>, "usage": {...}, "tool_calls": int}."""
    whitelist = {d["uuid"] for d in devices}
    agent = Agent(
        model=model or _model(),
        system_prompt=build_system(devices),
        tools=[make_status_tool(whitelist, status_lookup)],
        callback_handler=None,
    )
    prompt = build_prompt(utterance, devices, preferences, history, channel)
    result = agent(prompt, structured_output_model=PlanModel)
    plan: PlanModel = result.structured_output
    usage = dict(result.metrics.accumulated_usage or {})
    tool_calls = sum(m.call_count for m in result.metrics.tool_metrics.values())
    return {
        "plan": plan.model_dump(),
        "usage": {
            k: usage.get(k, 0)
            for k in ("inputTokens", "outputTokens", "totalTokens", "cacheReadInputTokens", "cacheWriteInputTokens")
        },
        "tool_calls": tool_calls,
        "cycles": result.metrics.cycle_count,
    }
