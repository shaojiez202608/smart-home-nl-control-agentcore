"""Long-term preference writes to Amazon Bedrock AgentCore Memory.

Implements the write-side rules from smarthome_core.memory_policy:
overwrite (one record per device and attribute) and expiry.

Listing memory records is eventually consistent: a record written seconds
ago may not be listed yet, so a delete-then-create overwrite can leave a
superseded record behind. Two rules keep the behaviour correct anyway:

* reads keep only the newest record per (device, attribute);
* every write deletes all superseded records it can see, so leftovers are
  removed by a later write.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import boto3
from smarthome_core.memory_policy import PREFERENCE_TTL_DAYS, PreferenceRecord


def preferences_namespace(user_id: str) -> str:
    return f"/members/{user_id}/preferences/"


class AgentCoreMemoryStore:
    def __init__(self):
        self.memory_id = os.environ["MEMORY_ID"]
        self.ttl_days = int(os.environ.get("PREFERENCE_TTL_DAYS", str(PREFERENCE_TTL_DAYS)))
        self.client = boto3.client("bedrock-agentcore")

    def list_texts(self, user_id: str) -> list[str]:
        resp = self.client.list_memory_records(
            memoryId=self.memory_id, namespace=preferences_namespace(user_id), maxResults=100
        )
        return [r["content"]["text"] for r in newest_per_key(resp.get("memoryRecordSummaries", []))]

    def upsert(self, user_id: str, records: list[PreferenceRecord]) -> None:
        ns = preferences_namespace(user_id)
        for rec in records:
            existing = self.client.list_memory_records(
                memoryId=self.memory_id,
                namespace=ns,
                maxResults=20,
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
            ).get("memoryRecordSummaries", [])
            if existing:
                self.client.batch_delete_memory_records(
                    memoryId=self.memory_id,
                    records=[{"memoryRecordId": r["memoryRecordId"], "namespace": ns} for r in existing],
                )
            self.client.batch_create_memory_records(
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
