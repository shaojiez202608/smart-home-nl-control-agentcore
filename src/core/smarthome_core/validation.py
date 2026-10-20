"""Deterministic validation of model output ("prompts are soft, code is hard").

Every action proposed by the model passes through ``validate_plan`` before
anything is sent to a device. The checks are:

1. the UUID is in the per-request whitelist (devices the user may control);
2. the action is defined for that device type in the catalog;
3. every parameter is known, well-typed and within bounds, and every
   required parameter is present;
4. duplicate actions on one device are collapsed; contradictory actions on
   one device (e.g. lock + unlock) cancel each other and trigger a
   clarification instead of guessing;
5. the number of actions per request is capped.

Rejected items are returned with reasons for logging and metrics. They are
never shown to the end user verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import actions_for
from .models import Device, Plan, PlannedAction, SkippedDevice

MAX_ACTIONS_PER_REQUEST = 20

# Pairs of actions that contradict each other when requested together.
_OPPOSITES = {
    frozenset({"turnOn", "turnOff"}),
    frozenset({"open", "close"}),
    frozenset({"lock", "unlock"}),
    frozenset({"play", "pause"}),
}


@dataclass
class Rejection:
    uuid: str
    action: str
    reason: str


@dataclass
class ValidationOutcome:
    actions: list[PlannedAction] = field(default_factory=list)
    skipped: list[SkippedDevice] = field(default_factory=list)
    rejected: list[Rejection] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # uuids


def validate_plan(plan: Plan, whitelist: dict[str, Device]) -> ValidationOutcome:
    out = ValidationOutcome()
    by_device: dict[str, list[PlannedAction]] = {}

    for act in plan.actions:
        device = whitelist.get(act.uuid)
        if device is None:
            out.rejected.append(Rejection(act.uuid, act.action, "uuid_not_in_whitelist"))
            continue
        spec = actions_for(device.type).get(act.action)
        if spec is None:
            out.rejected.append(Rejection(act.uuid, act.action, "action_not_supported"))
            continue
        allowed = {p.name: p for p in spec.params}
        unknown = set(act.params) - set(allowed)
        if unknown:
            out.rejected.append(
                Rejection(act.uuid, act.action, f"unknown_params:{sorted(unknown)}")
            )
            continue
        missing = set(allowed) - set(act.params)
        if missing:
            out.rejected.append(
                Rejection(act.uuid, act.action, f"missing_params:{sorted(missing)}")
            )
            continue
        errors = [e for p in spec.params if (e := p.check(act.params[p.name]))]
        if errors:
            out.rejected.append(Rejection(act.uuid, act.action, "; ".join(errors)))
            continue
        # Normalise numeric params to int (the model may emit 30.0).
        params = {
            k: int(v) if allowed[k].type == "int" else v for k, v in act.params.items()
        }
        by_device.setdefault(act.uuid, []).append(
            PlannedAction(act.uuid, act.action, params, act.params_source)
        )

    for uuid, acts in by_device.items():
        names = {a.action for a in acts}
        if any(pair <= names for pair in _OPPOSITES):
            out.conflicts.append(uuid)
            for a in acts:
                out.rejected.append(Rejection(uuid, a.action, "contradictory_actions"))
            continue
        seen: set[tuple[str, tuple]] = set()
        for a in acts:
            key = (a.action, tuple(sorted(a.params.items())))
            if key in seen:
                continue
            seen.add(key)
            out.actions.append(a)

    if len(out.actions) > MAX_ACTIONS_PER_REQUEST:
        for a in out.actions[MAX_ACTIONS_PER_REQUEST:]:
            out.rejected.append(Rejection(a.uuid, a.action, "too_many_actions"))
        out.actions = out.actions[:MAX_ACTIONS_PER_REQUEST]

    # Only keep "skipped" entries that reference real devices, so the model
    # cannot inject arbitrary text about devices the user does not own.
    acted = {a.uuid for a in out.actions}
    for s in plan.skipped:
        if s.uuid in whitelist and s.uuid not in acted:
            out.skipped.append(SkippedDevice(s.uuid, s.reason[:200]))
    return out
