"""Write-side governance for long-term memory.

Lesson learned in testing: writing a "preference" on every on/off event
floods long-term memory with samples that carry no signal and buries real
habits. The rules implemented here are:

* **Only explicit preferences** - an action produces a preference record only
  if it carries a taste parameter (brightness, colour temperature, target
  temperature, volume) whose value the *user* stated in this utterance. This
  is decided in code: the value must appear in the utterance *as that
  parameter* - next to its unit ("30%", "26度", "4000K") or after a setting
  phrase ("亮度调到30", "调到30") - so "30分钟后..." does not count. Values the
  model says it took from remembered preferences are never written back as new
  preferences. The model's own ``params_source`` label is not trusted to say
  "user": in testing it labelled stated values as defaults.
* **Overwrite, do not append** - one record per (device, attribute).
* **Age out unless used** - records expire ``PREFERENCE_TTL_DAYS`` after they
  were last written. A dispatched action that used a remembered value
  rewrites the record once it is older than ``REFRESH_AFTER_DAYS``, so a
  preference in regular use does not expire; one that is never used again
  does.

Aliases ("床头小蓝") are stored the same way with ``attribute="alias"``.
They are auxiliary context only: the platform's explicit device names stay
authoritative, an alias must resolve to a device the user may control, and an
alias already used for another device (by the platform or by memory) is
refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .catalog import PREFERENCE_PARAMS
from .models import AliasUpdate, Device, PlannedAction

PREFERENCE_TTL_DAYS = 90
REFRESH_AFTER_DAYS = 7
MAX_ALIAS_LEN = 20

_CN_DIGITS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUMBER = re.compile(r"\d+|[一二两三四五六七八九]?十[一二三四五六七八九]?")

# Units written right after the number.
_UNITS = {
    "brightness": ("%", "％", "percent"),
    "volume": ("%", "％"),
    "kelvin": ("k", "K", "开尔文"),
    "celsius": ("度", "℃", "°"),
}
# Words that, shortly before the number, say which parameter is being set.
_KEYWORDS = {
    "brightness": ("亮度",),
    "volume": ("音量", "声音"),
    "kelvin": ("色温",),
    "celsius": ("温度", "空调"),
}
_SETTERS = ("调到", "调成", "调至", "设为", "设成", "设置为", "设置成", "开到", "改成", "改为")
# A number directly followed by one of these is a time or a count, never a parameter value.
_NOT_A_VALUE = ("分钟", "分", "秒", "小时", "点", "号", "个", "次", "天", "岁", "月", "年")
_WINDOW = 6


@dataclass(frozen=True)
class PreferenceRecord:
    device_uuid: str
    attribute: str
    value: str
    text: str
    created_at: str = ""  # ISO timestamp from the store; empty for records not yet written


def display_name(d: Device) -> str:
    """'主卧的床头灯', or just '主卧灯' when the name already starts with the room."""
    if not d.room or d.name.startswith(d.room):
        return d.name
    return f"{d.room}的{d.name}"


def _value(token: str) -> int:
    if token.isdigit():
        return int(token)
    tens, _, ones = token.partition("十")
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


def stated_values(utterance: str) -> dict[str, set[int]]:
    """Numbers the user stated for each preference parameter, by unit or by a setting phrase."""
    out: dict[str, set[int]] = {p: set() for p in PREFERENCE_PARAMS}
    for m in _NUMBER.finditer(utterance):
        value = _value(m.group())
        after = utterance[m.end() : m.end() + 4].lstrip()
        before = utterance[max(0, m.start() - _WINDOW) : m.start()]
        if after.startswith(_NOT_A_VALUE):
            continue
        for param in PREFERENCE_PARAMS:
            by_unit = after.startswith(_UNITS.get(param, ()))
            by_word = any(k in before for k in _KEYWORDS.get(param, ()))
            by_setter = any(before.rstrip().endswith(s) for s in _SETTERS)
            if by_unit or by_word or by_setter:
                out[param].add(value)
    return out


def preferences_from_actions(
    actions: list[PlannedAction], whitelist: dict[str, Device], utterance: str
) -> list[PreferenceRecord]:
    """Return the preference records worth writing for these *dispatched* actions."""
    stated = stated_values(utterance)
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
            if not isinstance(value, int) or isinstance(value, bool) or value not in stated[name]:
                continue
            out.append(
                PreferenceRecord(
                    device_uuid=device.uuid,
                    attribute=name,
                    value=str(value),
                    text=f"用户偏好：{display_name(device)} 的 {name} 常用值为 {value}",
                )
            )
    return out


def refreshes_for_used_preferences(
    actions: list[PlannedAction],
    known: list[PreferenceRecord],
    now_iso: str,
    refresh_after_days: int = REFRESH_AFTER_DAYS,
) -> list[PreferenceRecord]:
    """Records to rewrite because a dispatched action used their remembered value.

    Rewriting resets the record's expiry. A record is refreshed at most once per
    ``refresh_after_days`` so regular use does not turn every request into a write.
    """
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return []
    by_key = {(r.device_uuid, r.attribute): r for r in known if r.attribute != "alias"}
    out = []
    for act in actions:
        if act.params_source != "preference":
            continue
        for name, value in act.params.items():
            rec = by_key.get((act.uuid, name))
            if rec is None or rec.value != str(value):
                continue
            try:
                created = datetime.fromisoformat(rec.created_at)
            except ValueError:
                continue
            if created.tzinfo is None or now - created >= timedelta(days=refresh_after_days):
                out.append(PreferenceRecord(rec.device_uuid, rec.attribute, rec.value, rec.text))
    return out


def memory_aliases(records: list[PreferenceRecord], whitelist: dict[str, Device]) -> dict[str, set[str]]:
    """Remembered aliases per device uuid, for devices the user may still control."""
    out: dict[str, set[str]] = {}
    for r in records:
        if r.attribute == "alias" and r.device_uuid in whitelist and r.value:
            out.setdefault(r.device_uuid, set()).add(r.value)
    return out


@dataclass(frozen=True)
class AliasDecision:
    record: PreferenceRecord | None
    conflict_with: Device | None = None


def alias_record(
    update: AliasUpdate,
    whitelist: dict[str, Device],
    remembered: dict[str, set[str]] | None = None,
) -> AliasDecision:
    device = whitelist.get(update.uuid)
    alias = update.alias.strip()
    if device is None or not alias or len(alias) > MAX_ALIAS_LEN:
        return AliasDecision(None)
    # App-defined names and aliases are authoritative and cannot be shadowed.
    for d in whitelist.values():
        if d.uuid != device.uuid and (d.name == alias or alias in d.aliases):
            return AliasDecision(None, conflict_with=d)
    # A remembered alias of another device is not silently reassigned either.
    for uuid, names in (remembered or {}).items():
        if uuid != device.uuid and alias in names and uuid in whitelist:
            return AliasDecision(None, conflict_with=whitelist[uuid])
    return AliasDecision(
        PreferenceRecord(
            device_uuid=device.uuid,
            attribute="alias",
            value=alias,
            text=f"用户把 {display_name(device)} 称为“{alias}”",
        )
    )
