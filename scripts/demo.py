"""Demo client for a deployed stack.

    uv run python scripts/demo.py say "我要睡觉了"
    uv run python scripts/demo.py pending
    uv run python scripts/demo.py confirm <token>
    uv run python scripts/demo.py devices
    uv run python scripts/demo.py receipt <commandId>
    uv run python scripts/demo.py speaker "开门"        # voice channel via AWS IoT Core

Uses the demo user created by seed_demo.py (default demo-alice).
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

import boto3
import requests
from common import id_token, region, stack_name_from_config, stack_outputs


def api(out: dict, token: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict, float]:
    t = time.perf_counter()
    r = requests.request(
        method, out["AppApiUrl"].rstrip("/") + path, json=body, headers={"Authorization": token}, timeout=30
    )
    return r.status_code, (r.json() if r.content else {}), (time.perf_counter() - t) * 1000


def speaker_roundtrip(out: dict, speaker_id: str, utterance: str, timeout: float = 20.0) -> dict | None:
    """Publish an utterance as the speaker and wait for the reply over MQTT (WebSocket, SigV4)."""
    from awscrt import auth, mqtt
    from awsiot import mqtt_connection_builder

    endpoint = out["IotDataEndpoint"].removeprefix("https://")
    reply_topic = f"{out['TopicPrefix']}/speakers/{speaker_id}/reply"
    received: dict = {}
    conn = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=endpoint,
        region=region(),
        credentials_provider=auth.AwsCredentialsProvider.new_default_chain(),
        client_id=f"demo-{uuid.uuid4().hex[:8]}",
        clean_session=True,
    )
    conn.connect().result(10)
    fut, _ = conn.subscribe(
        topic=reply_topic, qos=mqtt.QoS.AT_LEAST_ONCE,
        callback=lambda topic, payload, **kw: received.update(json.loads(payload)),
    )
    fut.result(10)
    boto3.client("iot-data", region_name=region(), endpoint_url=out["IotDataEndpoint"]).publish(
        topic=f"{out['TopicPrefix']}/speakers/{speaker_id}/utterance",
        qos=1,
        payload=json.dumps({"utterance": utterance}, ensure_ascii=False).encode(),
    )
    deadline = time.time() + timeout
    while not received and time.time() < deadline:
        time.sleep(0.2)
    conn.disconnect().result(10)
    return received or None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["say", "pending", "confirm", "devices", "receipt", "speaker"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--user", default="demo-alice")
    ap.add_argument("--speaker-id", default="spk-home-a-living")
    ap.add_argument("--config", default="config/sandbox.json")
    a = ap.parse_args()
    out = stack_outputs(stack_name_from_config(a.config))
    if a.command == "speaker":
        print(json.dumps(speaker_roundtrip(out, a.speaker_id, a.arg or ""), ensure_ascii=False, indent=2))
        return
    token = id_token(out, a.user)
    if a.command == "say":
        status, body, ms = api(out, token, "POST", "/v1/commands", {"utterance": a.arg})
    elif a.command == "pending":
        status, body, ms = api(out, token, "GET", "/v1/confirmations")
    elif a.command == "confirm":
        status, body, ms = api(out, token, "POST", "/v1/confirmations", {"token": a.arg})
    elif a.command == "devices":
        status, body, ms = api(out, token, "GET", "/v1/devices")
    else:
        status, body, ms = api(out, token, "GET", f"/v1/commands/{a.arg}")
    print(f"HTTP {status}  client round trip {ms:.0f} ms")
    print(json.dumps(body, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
