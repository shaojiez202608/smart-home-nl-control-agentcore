"""Virtual device fleet (simulation only).

Subscribed through an AWS IoT rule to ``<prefix>/devices/+/cmd``. It plays the
role of the physical devices: applies the command to an in-message state
fragment and reports back on ``<prefix>/devices/<uuid>/state`` after a short
random delay, so the asynchronous state path can be demonstrated end to end
without hardware. Replace with real devices in an actual integration.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import Any

import boto3
from smarthome_core.catalog import actions_for

iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))
TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")
MAX_DELAY_MS = int(os.environ.get("SIM_MAX_DELAY_MS", "800"))


def handler(event: dict, context: Any) -> None:
    device_uuid = str(event["uuid"])
    spec = actions_for(str(event.get("deviceType"))).get(str(event.get("action")))
    time.sleep(random.uniform(0.05, MAX_DELAY_MS / 1000))  # noqa: S311 - simulation jitter
    if spec is None:
        result, reported = "rejected", {}
    else:
        result, reported = "executed", {**spec.effect, **(event.get("params") or {})}
    iot.publish(
        topic=f"{TOPIC_PREFIX}/devices/{device_uuid}/state",
        qos=1,
        payload=json.dumps(
            {
                "commandId": event.get("commandId"),
                "result": result,
                "reported": reported,
                "ts": int(time.time()),
            }
        ).encode(),
    )
