"""Read-only checks before a deployment. Makes no changes in AWS.

    uv run python scripts/preflight.py --config config/sandbox.json [--expect-account 111122223333]

Checks: credentials and target account/Region (account id never printed), tool chain (uv, Node.js; no
Docker needed), model access profile in the Region, AgentCore availability in the Region, CDK
bootstrap, VPC / Elastic IP headroom, and that the stack does not already exist in a failed state.
Exits non-zero on any blocking problem.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from common import ROOT, region

PROBLEMS: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def problem(msg: str) -> None:
    PROBLEMS.append(msg)
    print(f"  FAIL  {msg}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sandbox.json")
    ap.add_argument("--expect-account", help="refuse to continue unless the credentials are for this account")
    a = ap.parse_args()
    cfg = json.loads((ROOT / a.config).read_text(encoding="utf-8"))
    r = region()
    print(f"Preflight for stack {cfg['stackName']} in {r}")

    try:
        ident = boto3.client("sts", region_name=r).get_caller_identity()
        acct = ident["Account"]
        ok("credentials valid (account id not printed; use --expect-account to pin it)")
        if a.expect_account and acct != a.expect_account:
            problem("credentials are not for the expected account")
    except (BotoCoreError, ClientError) as exc:
        problem(f"no usable AWS credentials ({type(exc).__name__})")
        return 1

    ok("uv found") if shutil.which("uv") else problem(
        "uv not found (builds the Lambda layer and agent package)"
    )
    ok("Node.js found") if shutil.which("node") else problem("Node.js not found (needed for the CDK CLI)")

    bedrock = boto3.client("bedrock", region_name=r)
    profiles = {
        p["inferenceProfileId"] for p in bedrock.list_inference_profiles()["inferenceProfileSummaries"]
    }
    ok(f"model profile {cfg['modelId']} available") if cfg["modelId"] in profiles else problem(
        f"model profile {cfg['modelId']} not listed in {r}"
    )

    try:
        boto3.client("bedrock-agentcore-control", region_name=r).list_agent_runtimes(maxResults=1)
        ok("AgentCore control plane reachable in the Region")
    except (BotoCoreError, ClientError) as exc:
        problem(f"AgentCore not usable in {r} ({type(exc).__name__})")

    cfn = boto3.client("cloudformation", region_name=r)
    try:
        cfn.describe_stacks(StackName="CDKToolkit")
        ok("CDK bootstrap stack present")
    except ClientError:
        problem("CDK bootstrap missing: run `npx aws-cdk@latest bootstrap` for this account/Region")
    try:
        status = cfn.describe_stacks(StackName=cfg["stackName"])["Stacks"][0]["StackStatus"]
        if status.endswith("FAILED") or status == "ROLLBACK_COMPLETE":
            problem(f"stack exists in state {status}; delete it before deploying")
        else:
            ok(f"stack exists ({status}); deploy will update it")
    except ClientError:
        ok("stack does not exist yet")

    ec2 = boto3.client("ec2", region_name=r)
    quotas = boto3.client("service-quotas", region_name=r)

    def headroom(code: str, used: int, need: int, what: str) -> None:
        try:
            limit = quotas.get_service_quota(
                ServiceCode="vpc" if code.startswith("L-F") else "ec2", QuotaCode=code
            )["Quota"]["Value"]
        except ClientError:
            print(f"  ?     could not read the {what} quota; {used} in use")
            return
        (ok if used + need <= limit else problem)(f"{what}: {used} in use, quota {int(limit)}, need {need}")

    headroom("L-F678F1CE", len(ec2.describe_vpcs()["Vpcs"]), 1, "VPCs")
    headroom("L-0263D0A3", len(ec2.describe_addresses()["Addresses"]), int(cfg["natGateways"]), "Elastic IPs")

    print("\nBLOCKED: " + "; ".join(PROBLEMS) if PROBLEMS else "\nPreflight passed.")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
