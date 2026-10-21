import pytest
from smarthome_core.models import Device, Plan, PlanParseError
from smarthome_core.policy import apply_policy
from smarthome_core.validation import MAX_ACTIONS_PER_REQUEST, validate_plan

from local.fakes import sample_home


@pytest.fixture
def wl():
    return {d.uuid: d for d in sample_home()}


def plan(*actions, skipped=()):
    return Plan.from_dict({"type": "control", "actions": list(actions), "skipped": list(skipped)})


def test_hallucinated_uuid_is_filtered(wl):
    out = validate_plan(plan({"uuid": "550e-made-up", "action": "turnOff"}), wl)
    assert out.actions == []
    assert out.rejected[0].reason == "uuid_not_in_whitelist"


def test_action_must_exist_for_device_type(wl):
    out = validate_plan(
        plan({"uuid": "dev-front-lock", "action": "setBrightness", "params": {"brightness": 5}}), wl
    )
    assert out.actions == []
    assert out.rejected[0].reason == "action_not_supported"


@pytest.mark.parametrize(
    "params,reason",
    [
        ({"brightness": 0}, "below minimum"),
        ({"brightness": 101}, "above maximum"),
        ({"brightness": "dim"}, "must be a number"),
        ({"brightness": True}, "must be a number"),
        ({}, "missing_params"),
        ({"brightness": 30, "color": "red"}, "unknown_params"),
    ],
)
def test_parameter_bounds(wl, params, reason):
    out = validate_plan(plan({"uuid": "dev-master-light", "action": "setBrightness", "params": params}), wl)
    assert out.actions == []
    assert reason in out.rejected[0].reason


def test_float_params_are_normalised(wl):
    out = validate_plan(
        plan({"uuid": "dev-master-light", "action": "setBrightness", "params": {"brightness": 30.0}}), wl
    )
    assert out.actions[0].params == {"brightness": 30}


def test_contradictory_actions_cancel_and_flag_conflict(wl):
    out = validate_plan(
        plan({"uuid": "dev-front-lock", "action": "lock"}, {"uuid": "dev-front-lock", "action": "unlock"}), wl
    )
    assert out.actions == []
    assert out.conflicts == ["dev-front-lock"]


def test_duplicates_collapse(wl):
    out = validate_plan(
        plan(
            {"uuid": "dev-living-light", "action": "turnOff"},
            {"uuid": "dev-living-light", "action": "turnOff"},
        ),
        wl,
    )
    assert len(out.actions) == 1


def test_action_cap():
    lights = {f"l{i}": Device(f"l{i}", f"灯{i}", "客厅", "light") for i in range(40)}
    many = [{"uuid": u, "action": "turnOn"} for u in lights]
    out = validate_plan(plan(*many), lights)
    assert len(out.actions) == MAX_ACTIONS_PER_REQUEST
    assert sum(r.reason == "too_many_actions" for r in out.rejected) == 20


def test_skipped_only_keeps_known_devices(wl):
    out = validate_plan(
        plan(skipped=[{"uuid": "dev-front-lock", "reason": "安全考虑"}, {"uuid": "ghost", "reason": "x"}]), wl
    )
    assert [s.uuid for s in out.skipped] == ["dev-front-lock"]


@pytest.mark.parametrize("uuid,action", [("dev-front-lock", "unlock"), ("dev-home-security", "disarm")])
def test_sensitive_actions_always_need_confirmation(wl, uuid, action):
    out = validate_plan(plan({"uuid": uuid, "action": action}), wl)
    pol = apply_policy(out.actions, wl)
    assert pol.allowed == [] and len(pol.needs_confirmation) == 1


@pytest.mark.parametrize(
    "uuid,action,params",
    [("dev-front-lock", "lock", {}), ("dev-home-security", "arm", {"mode": "away"})],
)
def test_policy_is_per_action_not_per_device_type(wl, uuid, action, params):
    out = validate_plan(plan({"uuid": uuid, "action": action, "params": params}), wl)
    assert len(apply_policy(out.actions, wl).allowed) == 1


def test_device_from_another_home_is_rejected(wl):
    out = validate_plan(plan({"uuid": "dev-b-front-lock", "action": "unlock"}), wl)
    assert out.actions == [] and out.rejected[0].reason == "uuid_not_in_whitelist"


@pytest.mark.parametrize(
    "raw", ["not json", "[]", '{"type": "dance"}', '{"type": "control", "actions": [{"x": 1}]}']
)
def test_malformed_model_output(raw):
    with pytest.raises(PlanParseError):
        Plan.from_json(raw)


def test_digit_strings_are_accepted_as_integers_but_other_strings_are_not(wl):
    ok = validate_plan(
        plan({"uuid": "dev-master-light", "action": "setBrightness", "params": {"brightness": "30"}}), wl
    )
    bad = validate_plan(
        plan({"uuid": "dev-master-light", "action": "setBrightness", "params": {"brightness": "30%"}}), wl
    )
    assert ok.actions[0].params == {"brightness": 30}
    assert bad.actions == []


def test_turn_on_with_a_value_becomes_the_matching_set_action(wl):
    out = validate_plan(
        plan({"uuid": "dev-master-light", "action": "turnOn", "params": {"brightness": 40}}), wl
    )
    assert [(a.action, a.params) for a in out.actions] == [("setBrightness", {"brightness": 40})]
    other = validate_plan(
        plan({"uuid": "dev-master-light", "action": "turnOn", "params": {"volume": 40}}), wl
    )
    assert other.actions == [] and "unknown_params" in other.rejected[0].reason
