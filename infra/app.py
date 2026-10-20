#!/usr/bin/env python3
"""CDK entrypoint.

    npx aws-cdk@latest deploy -c config=config/sandbox.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import aws_cdk as cdk
from cdk_nag import AwsSolutionsChecks

sys.path.insert(0, str(Path(__file__).resolve().parent))

from smarthome_infra.build import build_layer  # noqa: E402
from smarthome_infra.stack import NlControlStack  # noqa: E402

app = cdk.App()
config_path = Path(app.node.try_get_context("config") or "config/sandbox.json")
config = json.loads(config_path.read_text(encoding="utf-8"))

layer_dir = build_layer()

stack = NlControlStack(
    app,
    config["stackName"],
    config=config,
    layer_dir=layer_dir,
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_REGION") or "us-east-1",
    ),
    description="Natural-language smart home control prototype (not for production)",
)
for k, v in config.get("tags", {}).items():
    cdk.Tags.of(stack).add(k, v)

cdk.Validations.of(app).add_plugins(AwsSolutionsChecks(app))
app.synth()
