"""Plain data structures shared across components (stdlib only)."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

PLAN_TYPES = ("control", "query", "clarify", "chat", "alias")
PARAM_SOURCES = ("user", "preference", "default")
# Why the model left a device untouched. The model picks a code; the text shown to the user is a
# fixed string per code (messages.py), so model-written prose never reaches the reply.
SKIP_REASONS = ("already_in_state", "sensitive_action", "not_needed", "other")


@dataclass
class Device:
    uuid: str
    name: str
    room: str
    type: str
    state: dict[str, Any] = field(default_factory=dict)
    aliases: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Device:
        return cls(
            uuid=str(d["uuid"]),
            name=str(d["name"]),
            room=str(d.get("room", "")),
            type=str(d["type"]),
            state=dict(d.get("state") or {}),
            aliases=[str(a) for a in d.get("aliases") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PlannedAction:
    uuid: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    params_source: str = "default"
    name: str = ""  # echoed by the model; informational only, never used for routing


@dataclass
class SkippedDevice:
    uuid: str
    reason: str  # one of SKIP_REASONS once parsed by Plan.from_dict
    name: str = ""


@dataclass
class AliasUpdate:
    uuid: str
    alias: str


@dataclass
class Plan:
    """What the model *intends* to do. Never trusted without validation."""

    type: str
    actions: list[PlannedAction] = field(default_factory=list)
    skipped: list[SkippedDevice] = field(default_factory=list)
    # Interaction hint written by the model. For control plans it is
    # discarded and replaced by text built from dispatch results.
    message: str = ""
    alias: AliasUpdate | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Plan:
        if not isinstance(d, dict):
            raise PlanParseError("plan must be a JSON object")
        ptype = d.get("type")
        if ptype not in PLAN_TYPES:
            raise PlanParseError("unknown plan type")
        try:
            actions = [
                PlannedAction(
                    uuid=str(a["uuid"]),
                    action=str(a["action"]),
                    params=dict(a.get("params") or {}),
                    params_source=(
                        a.get("params_source") if a.get("params_source") in PARAM_SOURCES else "default"
                    ),
                    name=str(a.get("name") or ""),
                )
                for a in d.get("actions") or []
            ]
            skipped = [
                SkippedDevice(
                    uuid=str(s["uuid"]), reason=skip_reason(s.get("reason")), name=str(s.get("name") or "")
                )
                for s in d.get("skipped") or []
            ]
            alias_raw = d.get("alias")
            alias = (
                AliasUpdate(uuid=str(alias_raw["uuid"]), alias=str(alias_raw["alias"])) if alias_raw else None
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PlanParseError(f"malformed plan: {type(exc).__name__}") from exc
        return cls(
            type=ptype,
            actions=actions,
            skipped=skipped,
            message=str(d.get("message") or ""),
            alias=alias,
        )

    @classmethod
    def from_json(cls, raw: str) -> Plan:
        try:
            # NaN / Infinity are not JSON and 1e999 overflows to inf; Python's parser accepts both by default.
            return cls.from_dict(json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float))
        except (json.JSONDecodeError, RecursionError) as exc:
            raise PlanParseError(f"invalid JSON at position {getattr(exc, 'pos', '?')}") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlanParseError(ValueError):
    """The model output could not be interpreted as a Plan."""


def _reject_constant(name: str) -> Any:
    raise PlanParseError(f"non-finite number {name} in model output")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise PlanParseError("number out of range in model output")
    return value


def skip_reason(raw: Any) -> str:
    """Map whatever the model wrote to a fixed reason code; free text becomes "other"."""
    return raw if isinstance(raw, str) and raw in SKIP_REASONS else "other"


@dataclass
class DispatchResult:
    uuid: str
    action: str
    accepted: bool
    params: dict[str, Any] = field(default_factory=dict)
    command_id: str = ""
    error: str = ""
