import json
from pathlib import Path

import pytest

from infra.smarthome_infra.config import validate

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", ["sandbox", "handover"])
def test_shipped_configs_are_valid(name):
    validate(json.loads((ROOT / "config" / f"{name}.json").read_text()))


@pytest.mark.parametrize(
    "key,value",
    [
        ("removalPolicy", "keep"),
        ("resourcePrefix", "Bad_Prefix"),
        ("sessionIdleSeconds", 10),
        ("gatewayPolicyMode", "OFF"),
    ],
)
def test_bad_values_are_rejected(key, value):
    c = json.loads((ROOT / "config" / "sandbox.json").read_text())
    c[key] = value
    with pytest.raises(ValueError):
        validate(c)
