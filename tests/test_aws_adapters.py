"""Adapter tests against the real botocore service models (no network).

botocore's Stubber validates every request against the service model, so
these tests catch parameter-shape mistakes that in-memory fakes cannot.
"""

import sys
from pathlib import Path

import boto3
import pytest
from botocore.stub import ANY, Stubber

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "aws"))

from smarthome_aws.memory_store import AgentCoreMemoryStore, preferences_namespace  # noqa: E402
from smarthome_core.memory_policy import PreferenceRecord  # noqa: E402


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("MEMORY_ID", "mem-test-000001")
    monkeypatch.setenv("PREFERENCE_TTL_DAYS", "45")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    s = AgentCoreMemoryStore()
    s.client = boto3.client(
        "bedrock-agentcore", region_name="us-west-2", aws_access_key_id="x", aws_secret_access_key="x"
    )  # noqa: S106 - dummy, never used on the wire
    return s


OLD_ID = "mem-" + "a" * 40
REC = PreferenceRecord("dev-master-light", "brightness", "30", "用户偏好：主卧灯 的 brightness 常用值为 30")


def test_namespace_has_leading_and_trailing_slash():
    assert preferences_namespace("u1") == "/members/u1/preferences/"


def test_upsert_overwrites_previous_record(store):
    ns = preferences_namespace("u1")
    with Stubber(store.client) as stub:
        stub.add_response(
            "list_memory_records",
            {
                "memoryRecordSummaries": [
                    {
                        "memoryRecordId": OLD_ID,
                        "content": {"text": "x"},
                        "memoryStrategyId": "s",
                        "namespaces": [ns],
                        "createdAt": "2026-01-01T00:00:00Z",
                        "metadata": {
                            "deviceUuid": {"stringValue": "dev-master-light"},
                            "attribute": {"stringValue": "brightness"},
                        },
                    },
                    {
                        "memoryRecordId": "mem-" + "c" * 40,
                        "content": {"text": "another device"},
                        "memoryStrategyId": "s",
                        "namespaces": [ns],
                        "createdAt": "2026-01-01T00:00:00Z",
                        "metadata": {
                            "deviceUuid": {"stringValue": "dev-living-light"},
                            "attribute": {"stringValue": "brightness"},
                        },
                    },
                ]
            },
            {"memoryId": "mem-test-000001", "namespace": ns, "maxResults": 100},
        )
        # Create first, then delete the superseded record: a failed create keeps the old preference.
        stub.add_response(
            "batch_create_memory_records",
            {"successfulRecords": [], "failedRecords": []},
            {
                "memoryId": "mem-test-000001",
                "records": ANY,
                "recordExpirySpecification": {"recordExpiryDurationDays": 45},
            },
        )
        stub.add_response(
            "batch_delete_memory_records",
            {"successfulRecords": [], "failedRecords": []},
            {"memoryId": "mem-test-000001", "records": [{"memoryRecordId": OLD_ID, "namespace": ns}]},
        )
        store.upsert("u1", [REC])
        stub.assert_no_pending_responses()


def test_upsert_without_existing_record_only_creates(store):
    ns = preferences_namespace("u1")
    with Stubber(store.client) as stub:
        stub.add_response(
            "list_memory_records",
            {"memoryRecordSummaries": []},
            {"memoryId": "mem-test-000001", "namespace": ns, "maxResults": 100},
        )
        stub.add_response(
            "batch_create_memory_records",
            {"successfulRecords": [], "failedRecords": []},
            {"memoryId": "mem-test-000001", "records": ANY, "recordExpirySpecification": ANY},
        )
        store.upsert("u1", [REC])
        stub.assert_no_pending_responses()


def test_cache_iam_token_survives_garbage_collection(monkeypatch):
    import gc

    from smarthome_aws.cache import IamTokenProvider

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "dummy")  # noqa: S105 - dummy value for signing only
    provider = IamTokenProvider(user_id="app-user", cache_name="my-cache", region="us-west-2")
    gc.collect()
    user, token = provider.get_credentials()
    assert user == "app-user"
    assert token.startswith("my-cache/?Action=connect&User=app-user&ResourceType=ServerlessCache")
    assert "X-Amz-Signature=" in token


def test_reads_keep_only_the_newest_record_per_device_and_attribute():
    from smarthome_aws.memory_store import newest_per_key

    def rec(value, created):
        return {
            "content": {"text": value},
            "createdAt": created,
            "metadata": {"deviceUuid": {"stringValue": "d1"}, "attribute": {"stringValue": "brightness"}},
        }

    out = newest_per_key([rec("30", "2026-10-20T10:00:00Z"), rec("40", "2026-10-20T10:00:05Z")])
    assert [r["content"]["text"] for r in out] == ["40"]


def test_failed_records_in_a_batch_create_raise(store):
    from smarthome_aws.memory_store import MemoryWriteError

    ns = preferences_namespace("u1")
    with Stubber(store.client) as stub:
        stub.add_response(
            "list_memory_records",
            {"memoryRecordSummaries": []},
            {"memoryId": "mem-test-000001", "namespace": ns, "maxResults": 100},
        )
        stub.add_response(
            "batch_create_memory_records",
            {
                "successfulRecords": [],
                "failedRecords": [
                    {
                        "memoryRecordId": OLD_ID,
                        "requestIdentifier": "r1",
                        "status": "FAILED",
                        "errorCode": 400,
                        "errorMessage": "bad",
                    }
                ],
            },
            {"memoryId": "mem-test-000001", "records": ANY, "recordExpirySpecification": ANY},
        )
        with pytest.raises(MemoryWriteError):
            store.upsert("u1", [REC])


def test_failed_create_keeps_the_old_record(store):
    """O3: nothing is deleted when the create fails."""
    ns = preferences_namespace("u1")
    old = {
        "memoryRecordId": OLD_ID,
        "content": {"text": "x"},
        "memoryStrategyId": "s",
        "namespaces": [ns],
        "createdAt": "2026-01-01T00:00:00Z",
    }
    with Stubber(store.client) as stub:
        stub.add_response("list_memory_records", {"memoryRecordSummaries": [old]}, None)
        stub.add_client_error("batch_create_memory_records", "ThrottlingException")
        with pytest.raises(Exception):  # noqa: B017 - botocore ClientError
            store.upsert("u1", [REC])
        stub.assert_no_pending_responses()  # no batch_delete was queued or called


def test_list_records_follows_next_token(store):
    """O2: reads are paginated and keep the newest record per (device, attribute)."""
    ns = preferences_namespace("u1")

    def summary(rid, value, created):
        return {
            "memoryRecordId": rid,
            "content": {"text": f"pref {value}"},
            "memoryStrategyId": "s",
            "namespaces": [ns],
            "createdAt": created,
            "metadata": {
                "deviceUuid": {"stringValue": "d1"},
                "attribute": {"stringValue": "brightness"},
                "value": {"stringValue": value},
            },
        }

    with Stubber(store.client) as stub:
        stub.add_response(
            "list_memory_records",
            {
                "memoryRecordSummaries": [summary("mem-" + "a" * 40, "30", "2026-10-01T00:00:00Z")],
                "nextToken": "t1",
            },
            {"memoryId": "mem-test-000001", "namespace": ns, "maxResults": 100},
        )
        stub.add_response(
            "list_memory_records",
            {"memoryRecordSummaries": [summary("mem-" + "b" * 40, "40", "2026-10-02T00:00:00Z")]},
            {"memoryId": "mem-test-000001", "namespace": ns, "maxResults": 100, "nextToken": "t1"},
        )
        records = store.list_records("u1")
    assert [(r.value, r.text) for r in records] == [("40", "pref 40")]
    assert records[0].created_at.startswith("2026-10-02")
