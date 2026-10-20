"""Asynchronous state path: device report -> platform state + cache + app push.

Subscribed through an AWS IoT rule to ``<prefix>/devices/+/state``. This path
has no waiting relationship with the request path: the acceptance response
has usually returned before the device reports.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from smarthome_aws.cache import ValkeyCache
from smarthome_core.orchestrator import state_key

ddb = boto3.resource("dynamodb")
devices_t = ddb.Table(os.environ["DEVICES_TABLE"])
members_t = ddb.Table(os.environ["MEMBERS_TABLE"])
commands_t = ddb.Table(os.environ["COMMANDS_TABLE"])
iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))
cache = ValkeyCache.from_env()
TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")
STATE_TTL = int(os.environ.get("STATE_CACHE_TTL_SECONDS", "86400"))


def handler(event: dict, context: Any) -> None:
    device_uuid = str(event["uuid"])
    reported = event.get("reported") or {}
    command_id = event.get("commandId")
    now = int(time.time())

    if command_id:
        commands_t.update_item(
            Key={"commandId": command_id},
            UpdateExpression="SET #s = :s, executedAt = :t",
            ConditionExpression="attribute_exists(commandId)",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": event.get("result", "executed"), ":t": now},
        )
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

    state = json.loads(json.dumps(item.get("state", {}), default=lambda d: int(d) if d == int(d) else float(d)))
    cache.set_json(state_key(device_uuid), state, STATE_TTL)

    members = members_t.query(IndexName="byHome", KeyConditionExpression=Key("homeId").eq(item["homeId"]))["Items"]
    payload = json.dumps({"uuid": device_uuid, "state": state, "commandId": command_id, "ts": now}).encode()
    for m in members:
        iot.publish(topic=f"{TOPIC_PREFIX}/members/{m['userId']}/events", qos=0, payload=payload)
