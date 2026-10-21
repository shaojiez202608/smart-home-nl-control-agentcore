"""Leftover-check helpers: pagination and bootstrap-asset identification (no network)."""

import hashlib
import json

import bootstrap_assets
import boto3
import verify_cleanup
from botocore.stub import Stubber


def client(service):
    return boto3.client(service, region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="x")


def test_items_follows_iam_pagination():
    iam = client("iam")
    role = {"Path": "/", "RoleId": "AROAEXAMPLE000000000", "CreateDate": "2026-01-01T00:00:00Z"}
    with Stubber(iam) as stub:
        stub.add_response(
            "list_roles",
            {
                "Roles": [{**role, "RoleName": "other", "Arn": "arn:aws:iam::111122223333:role/other"}],
                "IsTruncated": True,
                "Marker": "m1",
            },
            {},
        )
        stub.add_response(
            "list_roles",
            {
                "Roles": [
                    {
                        **role,
                        "RoleName": "proto-nl-sandbox-X",
                        "Arn": "arn:aws:iam::111122223333:role/proto-nl-sandbox-X",
                    }
                ],
                "IsTruncated": False,
            },
            {"Marker": "m1"},
        )
        names = [r["RoleName"] for r in verify_cleanup.items(iam, "list_roles", "Roles")]
    assert names == ["other", "proto-nl-sandbox-X"]  # the second page is not lost


def test_items_follows_next_token_for_non_paginated_operations():
    class Fake:
        calls = []

        def can_paginate(self, op):
            return False

        def list_things(self, **kw):
            self.calls.append(kw)
            return {"things": [1], "nextToken": "t"} if "nextToken" not in kw else {"things": [2]}

    f = Fake()
    assert list(verify_cleanup.items(f, "list_things", "things")) == [1, 2]


def test_mask_replaces_only_standalone_account_ids():
    h = "5a" * 32  # an asset hash: hex digits that contain runs of 12 digits must not be masked
    h = h[:10] + "123456789" + "012" + h[22:]
    account = "9" * 12  # built at run time: no 12-digit literal in the repository
    assert (
        verify_cleanup.mask(f"arn:aws:iam::{account}:role/x {h}") == f"arn:aws:iam::111122223333:role/x {h}"
    )


def template(description, keys):
    return {
        "Description": description,
        "Resources": {
            "OrchestratorFnABC": {
                "Type": "AWS::Lambda::Function",
                "Properties": {"Code": {"S3Key": keys[0]}},
            },
            "AWS679f53fac002430cb0da5b7982bd2287": {
                "Type": "AWS::Lambda::Function",
                "Properties": {"Code": {"S3Key": keys[1]}},
            },
        },
    }


def test_project_template_is_identified_by_hash_and_description():
    own, provider = "a" * 64 + ".zip", "b" * 64 + ".zip"
    body = json.dumps(template(bootstrap_assets.STACK_DESCRIPTION, [own, provider])).encode()
    key = hashlib.sha256(body).hexdigest() + ".json"
    t = bootstrap_assets.is_project_template(key, body)
    assert t is not None
    # Code of this project's resources is listed; CDK's generic provider package is not.
    assert bootstrap_assets.own_keys_in_template(t) == {own}


def test_other_templates_are_not_identified():
    body = json.dumps(template("someone else's stack", ["a" * 64 + ".zip", "b" * 64 + ".zip"])).encode()
    assert bootstrap_assets.is_project_template(hashlib.sha256(body).hexdigest() + ".json", body) is None
    ours = json.dumps(
        template(bootstrap_assets.STACK_DESCRIPTION, ["a" * 64 + ".zip", "b" * 64 + ".zip"])
    ).encode()
    assert bootstrap_assets.is_project_template("c" * 64 + ".json", ours) is None  # key is not its hash
