"""Simulated *existing* smart-home platform: device directory + device control service.

This Lambda stands in for the platform that the natural-language path must
not change. It owns device data, membership-based authorisation and command
dispatch to AWS IoT Core. In a real integration this component is replaced
by the platform's existing REST API; nothing in the NL path depends on how
it is implemented.

Routes (API Gateway REST, API key required):
    GET  /members/{userId}/devices
    GET  /devices/{uuid}/status?userId=
    POST /devices/{uuid}/commands        {"userId", "action", "params", "requestId"}
    GET  /commands/{commandId}?userId=
    GET  /speakers/{speakerId}
"""

from __future__ import annotations

import json
import os
import time
import uuid as uuidlib
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from smarthome_core.catalog import actions_for

ddb = boto3.resource("dynamodb")
devices_t = ddb.Table(os.environ["DEVICES_TABLE"])
members_t = ddb.Table(os.environ["MEMBERS_TABLE"])
commands_t = ddb.Table(os.environ["COMMANDS_TABLE"])
speakers_t = ddb.Table(os.environ["SPEAKERS_TABLE"])
iot = boto3.client("iot-data", endpoint_url=os.environ.get("IOT_DATA_ENDPOINT"))
TOPIC_PREFIX = os.environ.get("TOPIC_PREFIX", "smarthome")
RECEIPT_TTL_SECONDS = int(os.environ.get("RECEIPT_TTL_SECONDS", "86400"))


def _plain(o: Any) -> Any:
    if isinstance(o, Decimal):
        return int(o) if o == int(o) else float(o)
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    return o


def _resp(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(_plain(body), ensure_ascii=False),
    }


def _home_of(user_id: str) -> str | None:
    item = members_t.get_item(Key={"userId": user_id}).get("Item")
    return item["homeId"] if item else None


def _device_for(user_id: str, device_uuid: str) -> dict | None:
    """Authorisation stays in the platform: a user may only touch devices of their home."""
    home = _home_of(user_id)
    if not home:
        return None
    item = devices_t.get_item(Key={"uuid": device_uuid}).get("Item")
    return item if item and item["homeId"] == home else None


def list_devices(user_id: str) -> dict:
    home = _home_of(user_id)
    if not home:
        return _resp(404, {"error": "user has no home"})
    items = devices_t.query(IndexName="byHome", KeyConditionExpression=Key("homeId").eq(home))["Items"]
    fields = ("uuid", "name", "room", "type", "state", "aliases")
    return _resp(200, {"devices": [{k: i.get(k) for k in fields if k in i} for i in items]})


def device_status(user_id: str, device_uuid: str) -> dict:
    d = _device_for(user_id, device_uuid)
    if not d:
        return _resp(404, {"error": "device not found"})
    return _resp(200, {"uuid": d["uuid"], "name": d["name"], "state": d.get("state", {}), "online": True})


def send_command(device_uuid: str, body: dict) -> dict:
    user_id = str(body.get("userId", ""))
    d = _device_for(user_id, device_uuid)
    if not d:
        return _resp(404, {"error": "device not found"})
    action, params = body.get("action"), body.get("params") or {}
    spec = actions_for(d["type"]).get(action)
    # The existing platform validates commands on its own, independent of the NL path.
    if (
        spec is None
        or set(params) != {p.name for p in spec.params}
        or any(p.check(params[p.name]) for p in spec.params)
    ):
        return _resp(400, {"error": "invalid command"})
    command_id = uuidlib.uuid4().hex
    now = int(time.time())
    commands_t.put_item(
        Item={
            "commandId": command_id,
            "userId": user_id,
            "uuid": device_uuid,
            "action": action,
            "params": params,
            "requestId": str(body.get("requestId", "")),
            "status": "accepted",
            "acceptedAt": now,
            "expiresAt": now + RECEIPT_TTL_SECONDS,
        }
    )
    iot.publish(
        topic=f"{TOPIC_PREFIX}/devices/{device_uuid}/cmd",
        qos=1,
        payload=json.dumps(
            {"commandId": command_id, "deviceType": d["type"], "action": action, "params": params}
        ).encode(),
    )
    # Accepted for delivery; execution is reported asynchronously by the device.
    return _resp(202, {"commandId": command_id, "status": "accepted"})


def command_receipt(user_id: str, command_id: str) -> dict:
    item = commands_t.get_item(Key={"commandId": command_id}).get("Item")
    if not item or item["userId"] != user_id:
        return _resp(404, {"error": "command not found"})
    keep = ("commandId", "uuid", "action", "params", "status", "acceptedAt", "executedAt")
    return _resp(200, {k: item[k] for k in keep if k in item})


def speaker(speaker_id: str) -> dict:
    item = speakers_t.get_item(Key={"speakerId": speaker_id}).get("Item")
    if not item:
        return _resp(404, {"error": "speaker not found"})
    return _resp(200, {"speakerId": speaker_id, "ownerUserId": item["ownerUserId"]})


def handler(event: dict, context: Any) -> dict:
    m, r = event["httpMethod"], event["resource"]
    p = event.get("pathParameters") or {}
    q = event.get("queryStringParameters") or {}
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "invalid json"})
    if m == "GET" and r == "/members/{userId}/devices":
        return list_devices(p["userId"])
    if m == "GET" and r == "/devices/{uuid}/status":
        return device_status(q.get("userId", ""), p["uuid"])
    if m == "POST" and r == "/devices/{uuid}/commands":
        return send_command(p["uuid"], body)
    if m == "GET" and r == "/commands/{commandId}":
        return command_receipt(q.get("userId", ""), p["commandId"])
    if m == "GET" and r == "/speakers/{speakerId}":
        return speaker(p["speakerId"])
    return _resp(404, {"error": "not found"})
