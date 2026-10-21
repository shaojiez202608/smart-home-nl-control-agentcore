"""Asynchronous state path: device report -> platform state + cache + app push.

Subscribed through an AWS IoT rule to ``<prefix>/devices/+/state``. This path
has no waiting relationship with the request path: the acceptance response
has usually returned before the device reports. Reported fields are filtered
against the catalog, a receipt is only updated by the device it was sent to,
and the accepted -> reported latency is published as an EMF metric.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from smarthome_aws.cache import ValkeyCache
from smarthome_core.catalog import COMMAND_RESULTS, filter_reported_state
from smarthome_core.orchestrator import state_key

log = logging.getLogger("state_sync")
log.setLevel(logging.INFO)

ddb = boto3.resource("dynamodb")
devices_t = ddb.Table(os.environ["DEVICES_TABLE"])
members_t = ddb.Table(os.environ["MEMBERS_TABLE"])
commands_t = ddb.Table(os.environ["COMMANDS_TABLE"])
iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))
cache = ValkeyCache.from_env()
TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")
STATE_TTL = int(os.environ.get("STATE_CACHE_TTL_SECONDS", "86400"))
METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "SmartHomeNL")


def handler(event: dict, context: Any) -> None:
    device_uuid = str(event.get("uuid", ""))[:128]
    command_id = event.get("commandId")
    now = int(time.time())
    device = devices_t.get_item(Key={"uuid": device_uuid}).get("Item")
    if not device:
        log.warning("state_report_unknown_device")
        return
    # Device reports are untrusted input that later reaches the model prompt: keep only the state
    # keys and values the catalog defines for this device type (THREAT_MODEL.md T2).
    reported, dropped = filter_reported_state(str(device.get("type")), event.get("reported") or {})
    if dropped:
        log.warning(
            "state_report_fields_dropped %s", json.dumps({"uuid": device_uuid, "fields": dropped[:20]})
        )
    result = event.get("result", "executed")
    if result not in COMMAND_RESULTS:
        result = "failed"

    if isinstance(command_id, str) and command_id:
        try:
            item = commands_t.update_item(
                Key={"commandId": command_id},
                UpdateExpression="SET #s = :s, executedAt = :t",
                # The receipt must belong to the reporting device.
                ConditionExpression="attribute_exists(commandId) AND #u = :device",
                ExpressionAttributeNames={"#s": "status", "#u": "uuid"},
                ExpressionAttributeValues={":s": result, ":t": now, ":device": device_uuid},
                ReturnValues="ALL_NEW",
            )["Attributes"]
            accepted_ms = item.get("acceptedAtMs")
            if accepted_ms is not None:
                _emit_receipt_latency(int(time.time() * 1000) - int(accepted_ms), result)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            log.warning("receipt_rejected %s", json.dumps({"uuid": device_uuid}))
            reported = {}  # a report for someone else's command does not change state either
    if not reported:
        return

    names = {f"#k{i}": k for i, k in enumerate(reported)}
    values = {f":v{i}": v for i, v in enumerate(reported.values())}
    sets = ", ".join(f"#st.{n} = {v}" for n, v in zip(names, values, strict=True))
    item = devices_t.update_item(
        Key={"uuid": device_uuid},
        UpdateExpression=f"SET {sets}",
        ExpressionAttributeNames={"#st": "state", **names},
        ExpressionAttributeValues=values,
        ReturnValues="ALL_NEW",
    )["Attributes"]

    state = json.loads(
        json.dumps(item.get("state", {}), default=lambda d: int(d) if d == int(d) else float(d))
    )
    cache.set_json(state_key(device_uuid), state, STATE_TTL)

    members = members_t.query(IndexName="byHome", KeyConditionExpression=Key("homeId").eq(item["homeId"]))[
        "Items"
    ]
    payload = json.dumps({"uuid": device_uuid, "state": state, "commandId": command_id, "ts": now}).encode()
    for m in members:
        iot.publish(topic=f"{TOPIC_PREFIX}/members/{m['userId']}/events", qos=0, payload=payload)


def _emit_receipt_latency(ms: int, result: str) -> None:
    """EMF metric: command accepted by the platform -> device report received (FR-11)."""
    doc = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": METRICS_NAMESPACE,
                    "Dimensions": [["Service"]],
                    "Metrics": [
                        {"Name": "ReceiptLatency", "Unit": "Milliseconds"},
                        {"Name": f"Receipt{result.capitalize()}", "Unit": "Count"},
                    ],
                }
            ],
        },
        "Service": "state-sync",
        "ReceiptLatency": max(ms, 0),
        f"Receipt{result.capitalize()}": 1,
    }
    print(json.dumps(doc))  # noqa: T201 - EMF goes to stdout
