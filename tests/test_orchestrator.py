import pytest
from smarthome_core.memory_policy import preferences_from_actions
from smarthome_core.messages import FALLBACK_REPLY
from smarthome_core.models import PlannedAction
from smarthome_core.orchestrator import Orchestrator, devices_key, state_key

from local.fakes import FakePlatform, FakePreferenceStore, MemoryCache, ScriptedAgent

USER = "user-1"

SLEEP_PLAN = {
    "type": "control",
    "actions": [
        {"uuid": "dev-master-light", "action": "turnOff"},
        {"uuid": "dev-living-light", "action": "turnOff"},
        {"uuid": "dev-master-curtain", "action": "close"},
    ],
    "skipped": [{"uuid": "dev-front-lock", "reason": "安全考虑，睡觉场景不自动操作门锁"}],
    "message": "晚安！门锁已经帮你打开了。",  # model prose must be ignored for control
}


def make(outputs, platform=None):
    platform = platform or FakePlatform()
    prefs = FakePreferenceStore()
    cache = MemoryCache()
    orch = Orchestrator(
        cache=cache, directory=platform, agent=ScriptedAgent(outputs), dispatcher=platform, preferences=prefs
    )
    return orch, platform, prefs, cache


def test_bedtime_scene_dispatches_and_builds_reply_from_results():
    orch, platform, _, _ = make([SLEEP_PLAN])
    r = orch.handle(USER, "我要睡觉了")
    assert r.type == "control"
    assert {a["uuid"] for a in r.actions} == {"dev-master-light", "dev-living-light", "dev-master-curtain"}
    assert all(a["status"] == "accepted" for a in r.actions)
    assert "已下发指令" in r.message and "前门锁保持当前状态" in r.message
    assert "门锁已经帮你打开" not in r.message
    assert {"preload", "inference", "dispatch", "total"} <= set(r.timings_ms)


def test_device_context_is_preloaded_into_the_single_inference():
    orch, _, _, _ = make([SLEEP_PLAN])
    orch.handle(USER, "我要睡觉了")
    req = orch.agent.requests[0]
    assert {d["uuid"] for d in req.devices} >= {"dev-front-lock", "dev-living-ac"}
    assert any("setBrightness" in c for c in next(d for d in req.devices if d["type"] == "light")["commands"])
    assert len(req.session_id) >= 33


def test_cache_hit_avoids_platform_call_and_overlays_state():
    orch, platform, _, cache = make([SLEEP_PLAN, SLEEP_PLAN])
    orch.handle(USER, "我要睡觉了")
    platform.devices["dev-living-ac"].name = "renamed-on-platform"  # not visible until cache expires
    cache.set_json(state_key("dev-living-ac"), {"power": "on"}, 60)
    orch.handle(USER, "我要睡觉了")
    req = orch.agent.requests[1]
    ac = next(d for d in req.devices if d["uuid"] == "dev-living-ac")
    assert ac["name"] == "客厅空调" and ac["state"]["power"] == "on"
    assert cache.get_json(devices_key(USER)) is not None


def test_dispatch_failure_is_reported_honestly():
    platform = FakePlatform(fail_uuids={"dev-living-light"})
    orch, *_ = make([SLEEP_PLAN], platform)
    r = orch.handle(USER, "我要睡觉了")
    assert "下发失败：客厅灯" in r.message
    assert {a["uuid"]: a["status"] for a in r.actions}["dev-living-light"] == "failed"


def test_parse_failure_returns_fixed_fallback_not_raw_output():
    orch, platform, _, _ = make(["<thinking>I will turn off everything</thinking>"])
    r = orch.handle(USER, "我要睡觉了")
    assert r.type == "error" and r.message == FALLBACK_REPLY
    assert platform.dispatched == []


def test_unlock_is_held_until_confirmed_then_single_use():
    unlock = {"type": "control", "actions": [{"uuid": "dev-front-lock", "action": "unlock"}]}
    orch, platform, _, _ = make([unlock])
    r = orch.handle(USER, "帮我开一下门锁")
    assert platform.dispatched == []
    token = r.pending_confirmation["token"]
    assert "需要你在 App 中确认" in r.message
    assert orch.confirm("someone-else", token).type == "error"  # token is bound to the user ...
    # ... and the failed attempt consumed it, so a legit retry must start over.
    assert orch.confirm(USER, token).type == "error"


def test_confirmation_dispatches_for_the_right_user():
    unlock = {"type": "control", "actions": [{"uuid": "dev-front-lock", "action": "unlock"}]}
    orch, platform, _, _ = make([unlock])
    token = orch.handle(USER, "帮我开一下门锁").pending_confirmation["token"]
    r = orch.confirm(USER, token)
    assert r.type == "control" and platform.dispatched[0][1].action == "unlock"


def test_unlock_by_voice_is_held_and_listed_for_the_app():
    unlock = {"type": "control", "actions": [{"uuid": "dev-front-lock", "action": "unlock"}]}
    orch, platform, _, _ = make([unlock])
    r = orch.handle(USER, "开门", channel="speaker")
    assert platform.dispatched == []
    assert r.pending_confirmation["requested_via"] == "speaker"
    listed = orch.pending_confirmations(USER)
    assert [p["token"] for p in listed] == [r.pending_confirmation["token"]]
    orch.confirm(USER, listed[0]["token"])
    assert orch.pending_confirmations(USER) == []


def test_same_named_devices_trigger_clarification():
    turn_on_one = {"type": "control", "actions": [{"uuid": "dev-master-bedside", "action": "turnOn"}]}
    orch, platform, _, _ = make([turn_on_one])
    r = orch.handle(USER, "打开床头灯")
    assert r.type == "clarify" and platform.dispatched == []
    assert "主卧的床头灯" in r.message and "次卧的床头灯" in r.message


def test_room_name_disambiguates_same_named_devices():
    turn_on_one = {"type": "control", "actions": [{"uuid": "dev-master-bedside", "action": "turnOn"}]}
    orch, platform, _, _ = make([turn_on_one])
    r = orch.handle(USER, "打开主卧的床头灯")
    assert r.type == "control" and len(platform.dispatched) == 1


def test_targeting_all_same_named_devices_is_not_ambiguous():
    both = {
        "type": "control",
        "actions": [
            {"uuid": "dev-master-bedside", "action": "turnOff"},
            {"uuid": "dev-guest-bedside", "action": "turnOff"},
        ],
    }
    orch, platform, _, _ = make([both])
    orch.handle(USER, "把床头灯都关了")
    assert len(platform.dispatched) == 2


def test_scene_requests_are_not_subject_to_name_ambiguity():
    scene = {"type": "control", "actions": [{"uuid": "dev-master-bedside", "action": "turnOff"}]}
    orch, platform, _, _ = make([scene])
    orch.handle(USER, "我要睡觉了")
    assert len(platform.dispatched) == 1


def test_only_explicit_user_values_become_preferences():
    plan = {
        "type": "control",
        "actions": [
            {"uuid": "dev-master-light", "action": "setBrightness", "params": {"brightness": 30}, "params_source": "user"},
            {"uuid": "dev-living-light", "action": "setBrightness", "params": {"brightness": 60}, "params_source": "preference"},
            {"uuid": "dev-living-ac", "action": "turnOn"},
            {"uuid": "dev-master-curtain", "action": "setPosition", "params": {"position": 30}, "params_source": "user"},
        ],
    }
    orch, _, prefs, _ = make([plan])
    orch.handle(USER, "主卧灯调到30，客厅灯60")
    recs = prefs.for_user(USER)
    assert [(r.device_uuid, r.attribute, r.value) for r in recs] == [("dev-master-light", "brightness", "30")]


def test_preference_needs_the_value_in_the_utterance_regardless_of_model_label():
    wl = {d.uuid: d for d in FakePlatform().list_user_devices(USER)}
    claimed_user = [PlannedAction("dev-master-light", "setBrightness", {"brightness": 30}, "user")]
    labelled_default = [PlannedAction("dev-master-light", "setBrightness", {"brightness": 30}, "default")]
    assert preferences_from_actions(claimed_user, wl, "主卧灯调暗一点") == []
    assert preferences_from_actions(claimed_user, wl, "主卧灯调到三十") != []
    assert preferences_from_actions(labelled_default, wl, "主卧灯调到30") != []


def test_alias_must_resolve_and_not_shadow_other_names():
    ok = {"type": "alias", "alias": {"uuid": "dev-master-bedside", "alias": "小蓝"}}
    bad = {"type": "alias", "alias": {"uuid": "dev-master-bedside", "alias": "大灯"}}  # app alias of 客厅灯
    ghost = {"type": "alias", "alias": {"uuid": "ghost", "alias": "小红"}}
    orch, _, prefs, _ = make([ok, bad, ghost])
    assert orch.handle(USER, "以后主卧床头灯叫小蓝").type == "alias"
    assert orch.handle(USER, "把主卧床头灯叫大灯").type == "clarify"
    assert orch.handle(USER, "x").type == "clarify"
    assert len(prefs.for_user(USER)) == 1


def test_idempotency_key_replays_without_second_dispatch():
    orch, platform, _, _ = make([SLEEP_PLAN])
    r1 = orch.handle(USER, "我要睡觉了", idempotency_key="k1")
    r2 = orch.handle(USER, "我要睡觉了", idempotency_key="k1")
    assert r1.request_id == r2.request_id
    assert len(platform.dispatched) == 3


@pytest.mark.parametrize("ptype", ["query", "clarify", "chat"])
def test_non_control_replies_pass_through_trimmed(ptype):
    orch, platform, _, _ = make([{"type": ptype, "message": "x" * 1000}])
    r = orch.handle(USER, "客厅灯开着吗")
    assert r.type == ptype and len(r.message) == 300 and platform.dispatched == []
