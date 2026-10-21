import json
import re
from pathlib import Path

import pytest

from infra.smarthome_infra.config import validate
from infra.smarthome_infra.stack_prefixes import base_model_id

ROOT = Path(__file__).resolve().parents[1]


def example(name="sandbox"):
    return json.loads((ROOT / "config" / f"{name}.example.json").read_text())


@pytest.mark.parametrize("name", ["sandbox", "handover"])
def test_shipped_templates_are_valid(name):
    validate(example(name))


def test_templates_use_the_project_prefix_and_no_account_id():
    for name in ("sandbox", "handover"):
        text = (ROOT / "config" / f"{name}.example.json").read_text()
        c = json.loads(text)
        assert c["resourcePrefix"] == "proto-nl" and c["stackName"].startswith("proto-nl-")
        assert not re.search(r"(?<!\d)\d{12}(?!\d)", text)


@pytest.mark.parametrize(
    "key,value",
    [
        ("removalPolicy", "keep"),
        ("resourcePrefix", "Bad_Prefix"),
        ("sessionIdleSeconds", 10),
        ("gatewayPolicyMode", "OFF"),
        ("apiThrottle", {"rateLimit": 0, "burstLimit": 10}),
        ("apiThrottle", {"rateLimit": 50, "burstLimit": 10}),
        ("modelId", "not a model id"),
        ("tags", {"Purpose": "prototype"}),
        ("tags", {"Project": 1}),
        ("stackName", "other-stack"),
        ("modelMaxTokens", 0),
    ],
)
def test_bad_values_are_rejected(key, value):
    c = example()
    c[key] = value
    with pytest.raises(ValueError):
        validate(c)


@pytest.mark.parametrize(
    "model,base",
    [
        ("us.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"),
        ("jp.amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"),
        ("global.amazon.nova-2-lite-v1:0", "amazon.nova-2-lite-v1:0"),
        ("amazon.nova-lite-v1:0", "amazon.nova-lite-v1:0"),
    ],
)
def test_inference_profile_prefix_is_stripped(model, base):
    assert base_model_id(model) == base
