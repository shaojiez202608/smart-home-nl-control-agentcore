"""In-memory adapters for local mode and unit tests."""

from __future__ import annotations

import copy
import json
import re
import time
import uuid as uuidlib
from pathlib import Path
from typing import Any

from smarthome_core.catalog import actions_for
from smarthome_core.memory_policy import PreferenceRecord
from smarthome_core.models import Device, DispatchResult, PlannedAction
from smarthome_core.ports import AgentRequest


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}

    def _alive(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        exp, val = item
        if exp < time.time():
            self._data.pop(key, None)
            return None
        return val

    def get_json(self, key: str) -> Any | None:
        val = self._alive(key)
        return json.loads(val) if val is not None else None

    def mget_json(self, keys: list[str]) -> list[Any | None]:
        return [self.get_json(k) for k in keys]

    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._data[key] = (time.time() + ttl_seconds, json.dumps(value, ensure_ascii=False))

    def pop_json(self, key: str) -> Any | None:
        val = self.get_json(key)
        self._data.pop(key, None)
        return val


SAMPLE_DATA = Path(__file__).resolve().parent.parent / "sample-data" / "homes.json"


def sample_home(home_id: str = "home-a") -> list[Device]:
    """Synthetic demo home shared by local mode, tests, the eval set and the seed script."""
    data = json.loads(SAMPLE_DATA.read_text(encoding="utf-8"))
    home = next(h for h in data["homes"] if h["homeId"] == home_id)
    return [Device.from_dict(d) for d in home["devices"]]


class FakePlatform:
    """Plays both the device directory and the device control service."""

    def __init__(self, devices: list[Device] | None = None, fail_uuids: set[str] | None = None):
        self.devices = {d.uuid: d for d in (devices or sample_home())}
        self.fail_uuids = fail_uuids or set()
        self.dispatched: list[tuple[str, PlannedAction]] = []

    def list_user_devices(self, user_id: str) -> list[Device]:
        return [copy.deepcopy(d) for d in self.devices.values()]

    def dispatch(self, user_id: str, action: PlannedAction, request_id: str) -> DispatchResult:
        self.dispatched.append((user_id, action))
        if action.uuid in self.fail_uuids:
            return DispatchResult(action.uuid, action.action, False, error="device_offline")
        device = self.devices[action.uuid]
        spec = actions_for(device.type)[action.action]
        device.state.update(spec.effect)
        device.state.update(action.params)
        return DispatchResult(action.uuid, action.action, True, command_id=uuidlib.uuid4().hex)


class FakePreferenceStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], PreferenceRecord] = {}

    def upsert(self, user_id: str, records: list[PreferenceRecord]) -> None:
        for r in records:
            self.records[(user_id, r.device_uuid, r.attribute)] = r  # overwrite, not append

    def for_user(self, user_id: str) -> list[PreferenceRecord]:
        return [r for (u, _, _), r in self.records.items() if u == user_id]


class ScriptedAgent:
    """Returns pre-baked model outputs; used to test the pipeline deterministically."""

    def __init__(self, outputs: list[str | dict]):
        self.outputs = list(outputs)
        self.requests: list[AgentRequest] = []

    def plan(self, request: AgentRequest) -> str:
        self.requests.append(request)
        out = self.outputs.pop(0)
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


class RuleBasedAgent:
    """A tiny keyword planner so local mode works without model access.

    It is intentionally naive; its only purpose is to exercise the pipeline
    offline. Real planning is done by the model.
    """

    def plan(self, request: AgentRequest) -> str:
        u = request.utterance
        devs = request.devices
        if "睡" in u:
            actions, skipped = [], []
            for d in devs:
                if d["type"] == "light" and d["state"].get("power") != "off":
                    actions.append({"uuid": d["uuid"], "action": "turnOff"})
                elif d["type"] == "curtain":
                    actions.append({"uuid": d["uuid"], "action": "close"})
                elif d["type"] == "lock":
                    if d["state"].get("locked"):
                        skipped.append({"uuid": d["uuid"], "reason": "已经锁好"})
                    else:
                        actions.append({"uuid": d["uuid"], "action": "lock"})
            return json.dumps({"type": "control", "actions": actions, "skipped": skipped})
        if "开锁" in u or "解锁" in u:
            locks = [d for d in devs if d["type"] == "lock"]
            return json.dumps(
                {"type": "control", "actions": [{"uuid": d["uuid"], "action": "unlock"} for d in locks]}
            )
        m = re.search(r"(\d+)", u)
        target = next((d for d in devs if d["name"] in u), None)
        if target and target["type"] == "light":
            if m:
                return json.dumps(
                    {
                        "type": "control",
                        "actions": [
                            {
                                "uuid": target["uuid"],
                                "action": "setBrightness",
                                "params": {"brightness": int(m.group(1))},
                                "params_source": "user",
                            }
                        ],
                    }
                )
            act = "turnOff" if "关" in u else "turnOn"
            return json.dumps({"type": "control", "actions": [{"uuid": target["uuid"], "action": act}]})
        return json.dumps({"type": "chat", "reply": "离线模式只支持几个示例句子，例如“我要睡觉了”。"})
