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
            config=Config(connect_timeout=2, read_timeout=int(os.environ.get("AGENT_READ_TIMEOUT", "15")),
                          retries={"max_attempts": 1}),
        )

    def plan(self, request: AgentRequest) -> str:
        resp = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.arn,
            qualifier=self.qualifier,
            runtimeSessionId=request.session_id,
            # Lets AgentCore Identity issue a workload token bound to this end user.
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
                },
                ensure_ascii=False,
            ).encode(),
        )
        body = resp["response"].read()
        out = json.loads(body)
        usage = out.get("usage") or {}
        for k in ("inputTokens", "outputTokens"):
            if k in usage:
                self.metrics.put(f"Model{k[0].upper()}{k[1:]}", usage[k], "Count")
        self.metrics.put("AgentToolCalls", out.get("tool_calls", 0), "Count")
        return json.dumps(out["plan"], ensure_ascii=False)
