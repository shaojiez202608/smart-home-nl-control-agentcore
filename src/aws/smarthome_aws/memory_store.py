"""Long-term preference writes to Amazon Bedrock AgentCore Memory.

Implements the write-side rules from smarthome_core.memory_policy:
overwrite (one record per device and attribute) and expiry.
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
                recordExpirySpecification={"recordExpiryDurationDays": self.ttl_days, "action": "REMOVE"},
            )
