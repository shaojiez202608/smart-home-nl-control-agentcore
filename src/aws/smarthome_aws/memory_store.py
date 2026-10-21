"""Long-term preference writes to Amazon Bedrock AgentCore Memory.

Implements the write-side rules from smarthome_core.memory_policy:
overwrite (one record per device and attribute) and expiry.

Listing memory records is eventually consistent: a record written seconds
ago may not be listed yet, so a delete-then-create overwrite can leave a
superseded record behind. Two rules keep the behaviour correct anyway:

* reads keep only the newest record per (device, attribute);
* every write deletes all superseded records it can see, so leftovers are
  removed by a later write. The new record is created before the old ones are
  deleted, so a failed create never loses the previous preference.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime

import boto3
from smarthome_core.memory_policy import PREFERENCE_TTL_DAYS, PreferenceRecord

log = logging.getLogger(__name__)


MAX_PAGES = 20


class MemoryWriteError(RuntimeError):
    """AgentCore Memory rejected a record in a batch call."""


def preferences_namespace(user_id: str) -> str:
    return f"/members/{user_id}/preferences/"


class AgentCoreMemoryStore:
    def __init__(self):
        self.memory_id = os.environ["MEMORY_ID"]
        self.ttl_days = int(os.environ.get("PREFERENCE_TTL_DAYS", str(PREFERENCE_TTL_DAYS)))
        self.client = boto3.client("bedrock-agentcore")

    def _list(self, namespace: str, **kw) -> list[dict]:
        """All record summaries in a namespace (follows nextToken)."""
        out: list[dict] = []
        token = None
        for _ in range(MAX_PAGES):
            args = {"memoryId": self.memory_id, "namespace": namespace, "maxResults": 100, **kw}
            if token:
                args["nextToken"] = token
            resp = self.client.list_memory_records(**args)
            out += resp.get("memoryRecordSummaries", [])
            token = resp.get("nextToken")
            if not token:
                break
        return out

    def list_records(self, user_id: str) -> list[PreferenceRecord]:
        return [
            PreferenceRecord(
                device_uuid=_meta(r, "deviceUuid"),
                attribute=_meta(r, "attribute"),
                value=_meta(r, "value"),
                text=r["content"]["text"],
                created_at=_iso(r.get("createdAt")),
            )
            for r in newest_per_key(self._list(preferences_namespace(user_id)))
            if _meta(r, "deviceUuid") and _meta(r, "attribute")
        ]

    def list_texts(self, user_id: str) -> list[str]:
        return [r.text for r in self.list_records(user_id)]

    def upsert(self, user_id: str, records: list[PreferenceRecord]) -> None:
        """Create the new record first, then delete the superseded ones (a failed create keeps the old)."""
        ns = preferences_namespace(user_id)
        for rec in records:
            existing = self._list(
                ns,
                metadataFilters=[
                    {
                        "left": {"metadataKey": "deviceUuid"},
                        "operator": "EQUALS_TO",
                        "right": {"metadataValue": {"stringValue": rec.device_uuid}},
                    },
                    {
                        "left": {"metadataKey": "attribute"},
                        "operator": "EQUALS_TO",
                        "right": {"metadataValue": {"stringValue": rec.attribute}},
                    },
                ],
            )
            created = self.client.batch_create_memory_records(
                memoryId=self.memory_id,
                records=[
                    {
                        "requestIdentifier": uuid.uuid4().hex,
                        "namespaces": [ns],
                        "content": {"text": rec.text},
                        "timestamp": datetime.now(UTC),
                        "metadata": {
                            "deviceUuid": {"stringValue": rec.device_uuid},
                            "attribute": {"stringValue": rec.attribute},
                            "value": {"stringValue": rec.value},
                        },
                    }
                ],
                # Tagged union: exactly one of recordExpiryDurationDays / action may be set.
                recordExpirySpecification={"recordExpiryDurationDays": self.ttl_days},
            )
            # Batch APIs report per-record failures in the response body, not as exceptions.
            if created.get("failedRecords"):
                raise MemoryWriteError(json.dumps(created["failedRecords"], default=str))
            if existing:
                deleted = self.client.batch_delete_memory_records(
                    memoryId=self.memory_id,
                    records=[{"memoryRecordId": r["memoryRecordId"], "namespace": ns} for r in existing],
                )
                # A failed delete only leaves a superseded record behind (reads keep the newest).
                if deleted.get("failedRecords"):
                    log.warning("memory_delete_partial %s", json.dumps(deleted["failedRecords"], default=str))


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "")


def _meta(record: dict, key: str) -> str:
    return ((record.get("metadata") or {}).get(key) or {}).get("stringValue", "")


def newest_per_key(records: list[dict]) -> list[dict]:
    """Keep the newest record per (deviceUuid, attribute); records without those keys pass through."""
    best: dict[tuple[str, str], dict] = {}
    passthrough = []
    for r in records:
        key = (_meta(r, "deviceUuid"), _meta(r, "attribute"))
        if not all(key):
            passthrough.append(r)
            continue
        if key not in best or str(r.get("createdAt", "")) > str(best[key].get("createdAt", "")):
            best[key] = r
    return passthrough + list(best.values())
