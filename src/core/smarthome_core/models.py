"""Plain data structures shared across components (stdlib only)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

PLAN_TYPES = ("control", "query", "clarify", "chat", "alias")
PARAM_SOURCES = ("user", "preference", "default")


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
    reason: str
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
            raise PlanParseError(f"unknown plan type: {ptype!r}")
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
                    uuid=str(s["uuid"]), reason=str(s.get("reason", "")), name=str(s.get("name") or "")
                )
                for s in d.get("skipped") or []
            ]
            alias_raw = d.get("alias")
            alias = (
                AliasUpdate(uuid=str(alias_raw["uuid"]), alias=str(alias_raw["alias"])) if alias_raw else None
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise PlanParseError(f"malformed plan: {exc}") from exc
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
            return cls.from_dict(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise PlanParseError(f"invalid JSON: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PlanParseError(ValueError):
    """The model output could not be interpreted as a Plan."""


@dataclass
class DispatchResult:
    uuid: str
    action: str
    accepted: bool
    params: dict[str, Any] = field(default_factory=dict)
    command_id: str = ""
    error: str = ""
