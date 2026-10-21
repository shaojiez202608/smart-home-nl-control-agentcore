"""Identify this project's objects in the shared CDK bootstrap assets bucket. Read-only.

    uv run python scripts/bootstrap_assets.py --regions us-east-1 us-west-2 [--json out.json]

The bucket (cdk-hnb659fds-assets-<ACCOUNT>-<REGION>) is shared by every CDK app in the account, so
objects are matched by asset hash, never by upload time alone:

1. Templates. A CDK template asset's key is the SHA-256 of its content. A `.json` object counts as
   this project's template only if sha256(content) equals its key AND the template's Description is
   this stack's description (STACK_DESCRIPTION, unchanged since the first commit).
2. Code assets. The S3 keys that such a template references from this project's own resources
   (Lambda functions, the shared layer, the agent package of the AgentCore Runtime) are this project's.
   Keys referenced only by CDK's generic providers (custom-resource provider functions) are shared
   content-addressed packages and are never listed.
3. The current build. Keys in cdk.out/<stack>.assets.json and .deploy/*.assets.json for this project's
   own resources (same rule as 2, using the synthesized template).

Anything not identified this way is not listed (and so never deleted).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
STACK_DESCRIPTION = "Natural-language smart home control prototype (not for production)"
# Logical-id prefixes of resources whose code comes from this repository.
OWN_RESOURCES = (
    "PlatformApiFn",
    "VirtualDeviceFn",
    "StateSyncFn",
    "OrchestratorFn",
    "SharedLayer",
    "Runtime",
)
MAX_TEMPLATE_BYTES = 2_000_000
_KEY = re.compile(r"^[0-9a-f]{64}\.(zip|json)$")


def bucket_name(account: str, region: str) -> str:
    return f"cdk-hnb659fds-assets-{account}-{region}"


def own_keys_in_template(template: dict) -> set[str]:
    """S3 keys referenced by this project's own resources in a synthesized/deployed template."""
    keys: set[str] = set()
    for logical_id, res in (template.get("Resources") or {}).items():
        if not logical_id.startswith(OWN_RESOURCES):
            continue
        for found in re.findall(r"[0-9a-f]{64}\.zip", json.dumps(res.get("Properties", {}))):
            keys.add(found)
    return keys


def is_project_template(key: str, body: bytes) -> dict | None:
    if not key.endswith(".json") or hashlib.sha256(body).hexdigest() != key.removesuffix(".json"):
        return None
    try:
        template = json.loads(body)
    except ValueError:
        return None
    return template if template.get("Description") == STACK_DESCRIPTION else None


def local_manifest_keys() -> set[str]:
    """Keys of the current build and of saved deploy manifests (own resources only)."""
    keys: set[str] = set()
    for manifest in [*ROOT.glob("cdk.out/*.assets.json"), *ROOT.glob(".deploy/*.assets.json")]:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        tmpl_dir = manifest.parent
        for asset_id, f in data.get("files", {}).items():
            src = f.get("source", {}).get("path", "")
            if src.endswith(".template.json") and (tmpl_dir / src).exists():
                template = json.loads((tmpl_dir / src).read_text(encoding="utf-8"))
                if template.get("Description") == STACK_DESCRIPTION:
                    keys.add(f"{asset_id}.json")
                    keys |= own_keys_in_template(template)
    return keys


def list_versions(s3, bucket: str) -> list[dict]:
    out = []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
        out += [{**v, "Marker": False} for v in page.get("Versions", [])]
        out += [{**m, "Marker": True} for m in page.get("DeleteMarkers", [])]
    return out


def project_objects(region: str, account: str) -> tuple[list[dict], dict]:
    """Return ([{Key, VersionId, Marker, Reason}], summary) for this project's objects in one Region."""
    s3 = boto3.client("s3", region_name=region)
    bucket = bucket_name(account, region)
    try:
        versions = list_versions(s3, bucket)
    except s3.exceptions.NoSuchBucket:
        return [], {"bucket": "missing"}
    own: dict[str, str] = {k: "current build manifest" for k in local_manifest_keys()}
    for v in versions:
        if v["Marker"] or not v["Key"].endswith(".json") or not _KEY.match(v["Key"]):
            continue
        if v.get("Size", 0) > MAX_TEMPLATE_BYTES:
            continue
        body = s3.get_object(Bucket=bucket, Key=v["Key"], VersionId=v["VersionId"])["Body"].read()
        template = is_project_template(v["Key"], body)
        if template is None:
            continue
        own[v["Key"]] = "project template (sha256 = key, stack description)"
        for k in own_keys_in_template(template):
            own.setdefault(k, f"referenced by project template {v['Key'][:12]}")
    hits = [
        {"Key": v["Key"], "VersionId": v["VersionId"], "Marker": v["Marker"], "Reason": own[v["Key"]]}
        for v in versions
        if v["Key"] in own
    ]
    return hits, {"bucket": bucket, "versions_total": len(versions), "project_versions": len(hits)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regions", nargs="+", default=["us-east-1"])
    ap.add_argument("--json", help="write the list (keys, version ids) to this file")
    a = ap.parse_args()
    account = boto3.client("sts").get_caller_identity()["Account"]
    report = {}
    for r in a.regions:
        hits, summary = project_objects(r, account)
        report[r] = {
            "summary": {**summary, "bucket": "cdk-hnb659fds-assets-111122223333-" + r},
            "objects": hits,
        }
        print(f"{r}: {len(hits)} project object versions of {summary.get('versions_total', 0)} in the bucket")
        for h in hits:
            print(f"  {h['Key']} {h['VersionId']} {'(delete marker)' if h['Marker'] else ''} - {h['Reason']}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
