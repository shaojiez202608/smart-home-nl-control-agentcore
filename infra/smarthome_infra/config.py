"""Validation of the parameter file; synth fails with a clear message on bad input."""

from __future__ import annotations

import re
from typing import Any

REQUIRED = {
    "stackName": str,
    "resourcePrefix": str,
    "removalPolicy": str,
    "modelId": str,
    "modelMaxTokens": int,
    "topicPrefix": str,
    "logRetentionDays": int,
    "memoryEventExpiryDays": int,
    "preferenceTtlDays": int,
    "acceptanceLatencyTargetMs": int,
    "apiThrottle": dict,
    "natGateways": int,
    "enableWaf": bool,
    "gatewayPolicyMode": str,
    "sessionIdleSeconds": int,
    "runtimeMaxLifetimeSeconds": int,
    "tags": dict,
}


def validate(c: dict[str, Any]) -> dict[str, Any]:
    errors = [f"{k}: missing or not {t.__name__}" for k, t in REQUIRED.items() if not isinstance(c.get(k), t)]
    if errors:
        raise ValueError("invalid config: " + "; ".join(errors))
    checks = [
        (
            re.fullmatch(r"[a-z][a-z0-9-]{1,14}", c["resourcePrefix"]),
            "resourcePrefix: 2-15 chars, lowercase, digits, '-'",
        ),
        (
            re.fullmatch(r"[A-Za-z][A-Za-z0-9-]{0,127}", c["stackName"]),
            "stackName: CloudFormation stack name",
        ),
        (c["removalPolicy"] in ("destroy", "retain"), "removalPolicy: destroy | retain"),
        (c["gatewayPolicyMode"] in ("ENFORCE", "LOG_ONLY"), "gatewayPolicyMode: ENFORCE | LOG_ONLY"),
        (c["logRetentionDays"] in (1, 3, 5, 7, 14, 30, 90), "logRetentionDays: 1, 3, 5, 7, 14, 30 or 90"),
        (3 <= c["memoryEventExpiryDays"] <= 365, "memoryEventExpiryDays: 3-365"),
        (
            60 <= c["sessionIdleSeconds"] <= c["runtimeMaxLifetimeSeconds"],
            "sessionIdleSeconds: >= 60 and <= max lifetime",
        ),
        (c["runtimeMaxLifetimeSeconds"] <= 1209600, "runtimeMaxLifetimeSeconds: <= 1209600"),
        (1 <= c["natGateways"] <= 2, "natGateways: 1 or 2"),
        (re.fullmatch(r"[a-z0-9-]{1,32}", c["topicPrefix"]), "topicPrefix: lowercase, digits, '-'"),
        (
            all(_positive_int(c["apiThrottle"].get(k)) for k in ("rateLimit", "burstLimit"))
            and c["apiThrottle"]["burstLimit"] >= c["apiThrottle"]["rateLimit"],
            "apiThrottle: rateLimit and burstLimit are positive integers, burstLimit >= rateLimit",
        ),
        (
            re.fullmatch(r"([a-z]{2,6}(-[a-z]+)?\.)?[a-z0-9-]+\.[a-z0-9.-]+(:[0-9]+)?", c["modelId"]),
            "modelId: a Bedrock model id or inference profile id, e.g. us.amazon.nova-lite-v1:0",
        ),
        (16 <= c["modelMaxTokens"] <= 8192, "modelMaxTokens: 16-8192"),
        (1 <= c["preferenceTtlDays"] <= 3650, "preferenceTtlDays: 1-3650"),
        (100 <= c["acceptanceLatencyTargetMs"] <= 60000, "acceptanceLatencyTargetMs: 100-60000"),
        (
            all(
                isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= 128 and len(v) <= 256
                for k, v in c["tags"].items()
            )
            and "Project" in c["tags"],
            "tags: string keys (1-128) and values (<=256); a Project tag is required",
        ),
        (
            c["stackName"].startswith(c["resourcePrefix"]),
            "stackName must start with resourcePrefix (cleanup and leftover checks match on the prefix)",
        ),
    ]
    bad = [msg for passed, msg in checks if not passed]
    if bad:
        raise ValueError("invalid config: " + "; ".join(bad))
    return c


def _positive_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0
