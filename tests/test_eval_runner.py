"""The evaluation runner itself must be able to fail: a counterfactual sanity check."""

import copy
import json
from pathlib import Path

from scripts.run_eval import run_case

CASES = json.loads((Path(__file__).resolve().parents[1] / "sample-data" / "eval" / "cases.json").read_text())[
    "cases"
]


def _case(case_id):
    return copy.deepcopy(next(c for c in CASES if c["id"] == case_id))


def test_runner_flags_a_wrong_dispatch():
    case = _case("multi-device-explicit")
    case["model_output"] = {"type": "control", "actions": [{"uuid": "dev-living-ac", "action": "turnOn"}]}
    fails, _ = run_case(case, "mock")
    assert fails, "a plan that controls the wrong device must fail the case"


def test_runner_flags_a_missing_confirmation_hold():
    case = _case("sensitive-unlock-app")
    case["model_output"] = {"type": "chat", "message": "好的"}
    fails, _ = run_case(case, "mock")
    assert any("held for confirmation" in f for f in fails)
