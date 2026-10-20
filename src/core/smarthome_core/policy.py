"""Execution-layer policy for sensitive actions.

Policies are defined per *action*, not per device type: locking the front
door at bedtime is what users want, unlocking it is not something a language
model should decide alone.

Decisions:

* ``allow``   - dispatch immediately.
* ``confirm`` - hold the action and require an explicit second confirmation
                through an authenticated channel (the app API). The
                confirmation is a separate request bound to the same user and
                to a single-use, short-lived token.
* ``deny``    - never dispatch.

The policy runs in code after validation, independent of the prompt, so a
prompt regression or a prompt injection cannot bypass it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Device, PlannedAction

ALLOW = "allow"
CONFIRM = "confirm"
DENY = "deny"

# (device_type, action) -> decision. Every channel gets the same decision:
# a voice request for a sensitive action is held and must be confirmed in
# the app, because a voice channel cannot present a second factor.
SENSITIVE_ACTIONS: dict[tuple[str, str], str] = {
    ("lock", "unlock"): CONFIRM,
    ("security_system", "disarm"): CONFIRM,
}


@dataclass
class PolicyOutcome:
    allowed: list[PlannedAction] = field(default_factory=list)
    needs_confirmation: list[PlannedAction] = field(default_factory=list)
    denied: list[PlannedAction] = field(default_factory=list)


def apply_policy(actions: list[PlannedAction], whitelist: dict[str, Device]) -> PolicyOutcome:
    out = PolicyOutcome()
    for act in actions:
        decision = SENSITIVE_ACTIONS.get((whitelist[act.uuid].type, act.action), ALLOW)
        if decision == ALLOW:
            out.allowed.append(act)
        elif decision == CONFIRM:
            out.needs_confirmation.append(act)
        else:
            out.denied.append(act)
    return out


def is_sensitive(device_type: str, action: str) -> bool:
    return (device_type, action) in SENSITIVE_ACTIONS
