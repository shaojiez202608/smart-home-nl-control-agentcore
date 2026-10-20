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
            all(k in c["apiThrottle"] for k in ("rateLimit", "burstLimit")),
            "apiThrottle: rateLimit and burstLimit",
        ),
    ]
    bad = [msg for passed, msg in checks if not passed]
    if bad:
        raise ValueError("invalid config: " + "; ".join(bad))
    return c
