"""HTTP client for the existing platform API (device directory + control service)."""

from __future__ import annotations

import json
import os
from functools import cache
from urllib.parse import quote

import boto3
import urllib3
from smarthome_core.models import Device, DispatchResult, PlannedAction

_http = urllib3.PoolManager(
    maxsize=16, timeout=urllib3.Timeout(connect=1.0, read=2.0), retries=urllib3.Retry(1, backoff_factor=0.05)
)


@cache
def _api_key() -> str:
    sm = boto3.client("secretsmanager")
    secret = sm.get_secret_value(SecretId=os.environ["PLATFORM_API_KEY_SECRET_ARN"])["SecretString"]
    return json.loads(secret)["apiKey"]


class PlatformClient:
    def __init__(self, base_url: str | None = None):
        self.base = (base_url or os.environ["PLATFORM_API_URL"]).rstrip("/")

    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        resp = _http.request(
            method,
            self.base + path,
            body=json.dumps(body).encode() if body is not None else None,
            headers={"x-api-key": _api_key(), "content-type": "application/json"},
        )
        data = json.loads(resp.data or b"{}")
        return resp.status, data

    # DeviceDirectory
    def list_user_devices(self, user_id: str) -> list[Device]:
        status, data = self._req("GET", f"/members/{quote(user_id)}/devices")
        if status != 200:
            raise RuntimeError(f"platform list devices failed: {status}")
        return [Device.from_dict(d) for d in data["devices"]]

    # CommandDispatcher
    def dispatch(self, user_id: str, action: PlannedAction, request_id: str) -> DispatchResult:
        status, data = self._req(
            "POST",
            f"/devices/{quote(action.uuid)}/commands",
            {"userId": user_id, "action": action.action, "params": action.params, "requestId": request_id},
        )
        if status == 202:
            return DispatchResult(action.uuid, action.action, True, command_id=data.get("commandId", ""))
        return DispatchResult(action.uuid, action.action, False, error=str(data.get("error", status)))

    def speaker_owner(self, speaker_id: str) -> str | None:
        status, data = self._req("GET", f"/speakers/{quote(speaker_id)}")
        return data.get("ownerUserId") if status == 200 else None

    def command_receipt(self, user_id: str, command_id: str) -> tuple[int, dict]:
        return self._req("GET", f"/commands/{quote(command_id)}?userId={quote(user_id)}")
