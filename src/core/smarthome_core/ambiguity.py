"""Code-level guard against acting on an ambiguous device reference.

The platform's device names and explicit app aliases are the authoritative
naming source. When the user's words name a device that is not unique in the
home ("打开床头灯" with two devices called 床头灯) and the utterance does not
disambiguate it, the action is not executed; the user is asked instead.

Rules (all in code, independent of the model):

* A reference is ambiguous when a label in the utterance (device name, app
  alias, or a remembered alias) belongs to two or more devices.
* Targeting every device of the group is accepted only when the utterance
  says so with a universal quantifier ("都", "全部", "所有", ...). Without it,
  "打开床头灯" with both bedside lamps in the plan is still a question.
* A room name disambiguates only if it is the longest room name matched at
  that position: in "主卧室的台灯", "卧室" is part of "主卧室" and does not
  count as a mention of the room "卧室".
* Remembered aliases count as labels for detecting ambiguity only; they never
  make a device eligible on their own.

The check only applies when the ambiguous name actually appears in the
utterance. Scene requests ("我要睡觉了") never name devices, and the model's
choice there is governed by validation and policy, not by naming.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import Device, PlannedAction

# Words that make "all devices with this name" an explicit request.
UNIVERSAL_QUANTIFIERS = ("都", "全部", "所有", "全都", "每个", "每一个")


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


def _labels(d: Device, memory_aliases: dict[str, set[str]]) -> set[str]:
    return {d.name, *d.aliases, *memory_aliases.get(d.uuid, set())}


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    out, start = [], 0
    while needle and (i := text.find(needle, start)) >= 0:
        out.append((i, i + len(needle)))
        start = i + 1
    return out


def mentioned_rooms(utterance: str, rooms: set[str]) -> set[str]:
    """Rooms mentioned in the utterance, ignoring matches inside a longer matched room name."""
    spans = {r: _spans(utterance, r) for r in rooms if r}
    mentioned = set()
    for room, occurrences in spans.items():
        for s, e in occurrences:
            inside_longer = any(
                other != room
                and len(other) > len(room)
                and any(os <= s and e <= oe for os, oe in spans[other])
                for other in spans
            )
            if not inside_longer:
                mentioned.add(room)
                break
    return mentioned


def _quantified(utterance: str) -> bool:
    return any(q in utterance for q in UNIVERSAL_QUANTIFIERS)


def resolve_ambiguity(
    actions: list[PlannedAction],
    whitelist: dict[str, Device],
    utterance: str,
    memory_aliases: dict[str, set[str]] | None = None,
) -> AmbiguityOutcome:
    out = AmbiguityOutcome()
    aliases = memory_aliases or {}
    targeted = {a.uuid for a in actions}
    blocked: set[str] = set()
    seen_groups: set[frozenset[str]] = set()

    for act in actions:
        device = whitelist[act.uuid]
        for label in _labels(device, aliases):
            if not label or label not in utterance:
                continue
            group = [d for d in whitelist.values() if label in _labels(d, aliases)]
            if len(group) < 2:
                continue
            ids = frozenset(d.uuid for d in group)
            if ids <= targeted and _quantified(utterance):
                continue  # "把床头灯都关了": every same-named device, explicitly requested
            rooms = mentioned_rooms(utterance, {d.room for d in group})
            if device.room in rooms and sum(d.room == device.room for d in group) == 1:
                continue  # disambiguated by a room that is unique within the group
            blocked |= ids & targeted
            if ids not in seen_groups:
                seen_groups.add(ids)
                out.groups.append(sorted(group, key=lambda d: d.room))

    out.actions = [a for a in actions if a.uuid not in blocked]
    return out
