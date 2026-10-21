"""Deterministic validation of model output ("prompts are soft, code is hard").

Every action proposed by the model passes through ``validate_plan`` before
anything is sent to a device. The checks are:

1. the UUID is in the per-request whitelist (devices the user may control);
2. the action is defined for that device type in the catalog;
3. every parameter is known, well-typed and within bounds, and every
   required parameter is present;
4. duplicate actions on one device are collapsed; contradictory actions on
   one device cancel each other and trigger a clarification instead of
   guessing. Two actions contradict when they would set the same state key to
   different values (lock + unlock, turnOff + setBrightness, open +
   setPosition 50, two different brightness values);
5. the number of actions per request is capped.

Rejected items are returned with reasons for logging and metrics. They are
never shown to the end user verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import actions_for
from .models import Device, Plan, PlannedAction, SkippedDevice, skip_reason

MAX_ACTIONS_PER_REQUEST = 20

# "turn on with a value" is expressed by a set* action that also switches the
# device on. Only these explicit combinations are rewritten.
_TURN_ON_WITH_VALUE = {
    ("light", "brightness"): "setBrightness",
    ("light", "kelvin"): "setColorTemperature",
    ("air_conditioner", "celsius"): "setTemperature",
}


def _target_state(device_type: str, act: PlannedAction) -> dict:
    spec = actions_for(device_type)[act.action]
    return {**spec.effect, **act.params}


def _contradictory(device_type: str, acts: list[PlannedAction]) -> bool:
    seen: dict[str, object] = {}
    for a in acts:
        for k, v in _target_state(device_type, a).items():
            if k in seen and seen[k] != v:
                return True
            seen[k] = v
    return False


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
        if act.action == "turnOn" and len(act.params) == 1:
            target = _TURN_ON_WITH_VALUE.get((device.type, next(iter(act.params))))
            if target:
                act = PlannedAction(act.uuid, target, act.params, act.params_source, act.name)
        spec = actions_for(device.type).get(act.action)
        if spec is None:
            out.rejected.append(Rejection(act.uuid, act.action, "action_not_supported"))
            continue
        allowed = {p.name: p for p in spec.params}
        unknown = set(act.params) - set(allowed)
        if unknown:
            out.rejected.append(Rejection(act.uuid, act.action, f"unknown_params:{sorted(unknown)}"))
            continue
        missing = set(allowed) - set(act.params)
        if missing:
            out.rejected.append(Rejection(act.uuid, act.action, f"missing_params:{sorted(missing)}"))
            continue
        act.params = {k: _digits_to_int(v) if allowed[k].type == "int" else v for k, v in act.params.items()}
        errors = [e for p in spec.params if (e := p.check(act.params[p.name]))]
        if errors:
            out.rejected.append(Rejection(act.uuid, act.action, "; ".join(errors)))
            continue
        # Normalise numeric params to int (the model may emit 30.0).
        params = {k: int(v) if allowed[k].type == "int" else v for k, v in act.params.items()}
        by_device.setdefault(act.uuid, []).append(
            PlannedAction(act.uuid, act.action, params, act.params_source)
        )

    for uuid, acts in by_device.items():
        if _contradictory(whitelist[uuid].type, acts):
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

    # Only keep "skipped" entries that reference real devices. Reasons are fixed codes
    # (models.SKIP_REASONS); the reply text for each code is written by the application.
    acted = {a.uuid for a in out.actions}
    listed: set[str] = set()
    for s in plan.skipped:
        if s.uuid in whitelist and s.uuid not in acted and s.uuid not in listed:
            listed.add(s.uuid)
            out.skipped.append(SkippedDevice(s.uuid, skip_reason(s.reason)))
    return out


def _digits_to_int(v: object) -> object:
    """Models sometimes emit integers as strings ("30"); accept plain ASCII digit strings only."""
    if isinstance(v, str) and v.strip().isascii() and v.strip().isdigit():
        return int(v.strip())
    return v
