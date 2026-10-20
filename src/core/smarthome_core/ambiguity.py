"""Code-level guard against acting on an ambiguous device reference.

The platform's device names and explicit app aliases are the authoritative
naming source. When the user's words name a device that is not unique in the
home ("打开床头灯" with two devices called 床头灯) and the utterance does not
disambiguate it, the action is not executed; the user is asked instead.

The check only applies when the ambiguous name actually appears in the
utterance. Scene requests ("我要睡觉了") never name devices, and the model's
choice there is governed by validation and policy, not by naming.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Device, PlannedAction


@dataclass
class AmbiguityOutcome:
    actions: list[PlannedAction] = field(default_factory=list)
    groups: list[list[Device]] = field(default_factory=list)

    def question(self) -> str:
        if not self.groups:
            return ""
        parts = []
        for group in self.groups:
            options = "还是".join(f"{d.room}的{d.name}" for d in group)
            parts.append(f"你说的是{options}")
        return "；".join(parts) + "？"


def _labels(d: Device) -> set[str]:
    return {d.name, *d.aliases}


def resolve_ambiguity(
    actions: list[PlannedAction], whitelist: dict[str, Device], utterance: str
) -> AmbiguityOutcome:
    out = AmbiguityOutcome()
    targeted = {a.uuid for a in actions}
    blocked: set[str] = set()
    seen_groups: set[frozenset[str]] = set()

    for act in actions:
        device = whitelist[act.uuid]
        for label in _labels(device):
            if label not in utterance:
                continue
            group = [d for d in whitelist.values() if label in _labels(d)]
            if len(group) < 2:
                continue
            ids = frozenset(d.uuid for d in group)
            if ids <= targeted:
                continue  # "把床头灯都关了": every same-named device is targeted
            # Disambiguated by room name, and the room is unique within the group.
            if device.room and device.room in utterance and sum(d.room == device.room for d in group) == 1:
                continue
            blocked |= ids & targeted
            if ids not in seen_groups:
                seen_groups.add(ids)
                out.groups.append(sorted(group, key=lambda d: d.room))

    out.actions = [a for a in actions if a.uuid not in blocked]
    return out
