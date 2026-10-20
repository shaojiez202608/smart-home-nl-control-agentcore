"""Write-side governance for long-term memory.

Lesson learned in production: writing a "preference" on every on/off event
floods long-term memory with samples that carry no signal and buries real
habits. The rules implemented here are:

* **Only explicit preferences** - an action produces a preference record only
  if it carries a taste parameter (brightness, colour temperature, target
  temperature, volume) whose value the *user* stated in this utterance. This
  is decided in code: the value must appear in the utterance text, and values
  the model says it took from remembered preferences are never written back.
  The model's own ``params_source`` label is not trusted to say "user":
  in testing it labelled stated values as defaults.
* **Overwrite, do not append** - one record per (device, attribute). The
  writer deletes the previous record before creating the new one.
* **Age out** - records expire after ``PREFERENCE_TTL_DAYS`` without being
  refreshed.

Aliases ("床头小蓝") are stored the same way with ``attribute="alias"``.
They are auxiliary context only: the platform's explicit device names stay
authoritative, and an alias must resolve to a device the user may control.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .catalog import PREFERENCE_PARAMS
from .models import AliasUpdate, Device, PlannedAction

PREFERENCE_TTL_DAYS = 90
MAX_ALIAS_LEN = 20

_NUM = re.compile(r"\d+")
_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


@dataclass(frozen=True)
class PreferenceRecord:
    device_uuid: str
    attribute: str
    value: str
    text: str


def _numbers_in(utterance: str) -> set[int]:
    nums = {int(n) for n in _NUM.findall(utterance)}
    # Very small Chinese-numeral support ("三十度", "二十六度").
    for m in re.finditer(r"([一二两三四五六七八九])?十([一二三四五六七八九])?", utterance):
        tens = _CN_DIGITS.get(m.group(1), 1) if m.group(1) else 1
        ones = _CN_DIGITS.get(m.group(2), 0) if m.group(2) else 0
        nums.add(tens * 10 + ones)
    return nums


def preferences_from_actions(
    actions: list[PlannedAction], whitelist: dict[str, Device], utterance: str
) -> list[PreferenceRecord]:
    """Return the preference records worth writing for these *dispatched* actions."""
    stated = _numbers_in(utterance)
    out: list[PreferenceRecord] = []
    for act in actions:
        if act.params_source == "preference":
            continue
        device = whitelist.get(act.uuid)
        if device is None:
            continue
        for name, value in act.params.items():
            if name not in PREFERENCE_PARAMS:
                continue
            # Relative requests ("调暗一点") carry no number, so nothing is written.
            if not isinstance(value, (int, float)) or int(value) not in stated:
                continue
            out.append(
                PreferenceRecord(
                    device_uuid=device.uuid,
                    attribute=name,
                    value=str(value),
                    text=f"用户偏好：{device.room}{device.name} 的 {name} 常用值为 {value}",
                )
            )
    return out


def alias_record(update: AliasUpdate, whitelist: dict[str, Device]) -> PreferenceRecord | None:
    device = whitelist.get(update.uuid)
    alias = update.alias.strip()
    if device is None or not alias or len(alias) > MAX_ALIAS_LEN:
        return None
    # An alias must not shadow another device's official name.
    # App-defined names and aliases are authoritative and cannot be overridden.
    if any((d.name == alias or alias in d.aliases) and d.uuid != device.uuid for d in whitelist.values()):
        return None
    return PreferenceRecord(
        device_uuid=device.uuid,
        attribute="alias",
        value=alias,
        text=f"用户把 {device.room}{device.name} 称为“{alias}”",
    )
