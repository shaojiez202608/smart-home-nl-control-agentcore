"""Invokes the agent hosted on Amazon Bedrock AgentCore Runtime."""

from __future__ import annotations

import json
import os

import boto3
from botocore.config import Config
from smarthome_core.ports import AgentRequest, Metrics


class RuntimeAgentClient:
    def __init__(self, metrics: Metrics):
        self.arn = os.environ["AGENT_RUNTIME_ARN"]
        self.qualifier = os.environ.get("AGENT_RUNTIME_QUALIFIER", "DEFAULT")
        self.metrics = metrics
        self.client = boto3.client(
            "bedrock-agentcore",
            config=Config(
                connect_timeout=2,
                read_timeout=int(os.environ.get("AGENT_READ_TIMEOUT", "15")),
                retries={"max_attempts": 1},
            ),
        )

    def plan(self, request: AgentRequest) -> str:
        resp = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.arn,
            qualifier=self.qualifier,
            runtimeSessionId=request.session_id,
            # Required for SigV4 callers: without a runtime user id the runtime hands the container no
            # workload access token, and the agent cannot obtain its M2M token for the gateway from
            # AgentCore Identity (found in deployment testing, DECISIONS.md D25). The value is the verified
            # Cognito sub or the speaker's registered owner; only this function's role may invoke the runtime
            # and pass a user id (runtime resource policy).
            runtimeUserId=request.user_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps(
                {
                    "user_id": request.user_id,
                    "session_id": request.session_id,
                    "utterance": request.utterance,
                    "channel": request.channel,
                    "devices": request.devices,
                    "preferences": request.preferences,
                },
                ensure_ascii=False,
            ).encode(),
        )
        body = resp["response"].read()
        out = json.loads(body)
        usage = out.get("usage") or {}
        for k in ("inputTokens", "outputTokens", "cacheReadInputTokens"):
            if k in usage:
                self.metrics.put(f"Model{k[0].upper()}{k[1:]}", usage[k], "Count")
        self.metrics.put("AgentToolCalls", out.get("tool_calls", 0), "Count")
        if "tool_errors" in out:
            self.metrics.put("AgentToolErrors", out.get("tool_errors", 0), "Count")
        for k, v in (out.get("timings_ms") or {}).items():
            # Segments measured inside the runtime; the rest of LatencyInference is invocation overhead.
            self.metrics.put("AgentMemoryReadMs" if k == "memory_read" else "AgentModelMs", v)
        return json.dumps(out["plan"], ensure_ascii=False)
