"""Read-only checks before a deployment. Makes no changes in AWS.

    uv run python scripts/preflight.py --config config/sandbox.json

Checks: parameter file and its validation; tool chain (uv, Node.js, npx; Docker is reported but not
needed); credentials and the target account and Region, which you must confirm (only the last 4 digits
of the account id are shown; non-interactive runs set CONFIRM_ACCOUNT=<last 4 digits>); the model
profile in the Region; AgentCore availability; the CDK bootstrap stack; the account-level API Gateway
CloudWatch Logs role (the stack enables API logging but never changes this account setting); VPC and
Elastic IP headroom; and that the stack is not in a failed state. Exits non-zero on any blocking problem.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # noqa: S404
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from common import ROOT, region

sys.path.insert(0, str(ROOT))

from infra.smarthome_infra.stack_prefixes import INFERENCE_PROFILE_PREFIXES  # noqa: E402

PROBLEMS: list[str] = []


def ok(msg: str) -> None:
    print(f"  ok    {msg}")


def info(msg: str) -> None:
    print(f"  info  {msg}")


def problem(msg: str) -> None:
    PROBLEMS.append(msg)
    print(f"  FAIL  {msg}")


def confirm_account(last4: str, r: str) -> bool:
    if os.environ.get("CONFIRM_ACCOUNT") == last4:
        return True
    if not sys.stdin.isatty():
        problem("set CONFIRM_ACCOUNT=<last 4 digits of the account id> to confirm the target account")
        return False
    answer = input(
        f"  Deploy target: account ********{last4}, Region {r}. Type the last 4 digits to confirm: "
    )
    if answer.strip() != last4:
        problem("target account not confirmed; stop if this is not your intended non-production account")
        return False
    return True


def inference_profiles(bedrock) -> set[str]:
    ids: set[str] = set()
    token = None
    while True:
        resp = bedrock.list_inference_profiles(**({"nextToken": token} if token else {}))
        ids |= {p["inferenceProfileId"] for p in resp.get("inferenceProfileSummaries", [])}
        token = resp.get("nextToken")
        if not token:
            return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sandbox.json")
    a = ap.parse_args()
    path = ROOT / a.config
    print("== parameter file")
    if not path.exists():
        problem(f"{a.config} missing (run: make init)")
        return 1
    cfg = json.loads(path.read_text(encoding="utf-8"))
    try:
        from infra.smarthome_infra.config import validate

        validate(cfg)
        ok(f"{a.config} valid (stack {cfg['stackName']}, removalPolicy {cfg['removalPolicy']})")
    except ValueError as exc:
        problem(str(exc))
    r = region()

    print("== tools")
    for tool, why in (
        ("uv", "builds the Lambda layer and agent package"),
        ("node", "CDK CLI"),
        ("npx", "CDK CLI"),
    ):
        ok(tool) if shutil.which(tool) else problem(f"{tool} not found ({why})")
    docker = shutil.which("docker")
    running = (
        bool(docker) and subprocess.run([docker, "info"], capture_output=True, check=False).returncode == 0
    )  # noqa: S603
    info(
        f"docker {'running' if running else 'not running or not installed'} (not needed: no container build)"
    )

    print("== credentials and target")
    try:
        ident = boto3.client("sts", region_name=r).get_caller_identity()
    except (BotoCoreError, ClientError) as exc:
        problem(f"no usable AWS credentials ({type(exc).__name__})")
        return 1
    last4 = ident["Account"][-4:]
    kind = ident["Arn"].split(":")[5].split("/")[0]
    info(f"account ********{last4}, Region {r}, caller type {kind}")
    if kind == "user":
        info("long-term IAM user credentials in use; prefer short-lived role credentials")
    if confirm_account(last4, r):
        ok("target account and Region confirmed")

    print(f"== services in {r}")
    try:
        profiles = inference_profiles(boto3.client("bedrock", region_name=r))
        model = cfg["modelId"]
        if model.split(".")[0] in INFERENCE_PROFILE_PREFIXES:
            (ok if model in profiles else problem)(
                f"inference profile {model} {'listed' if model in profiles else 'not listed'}"
            )
        else:
            boto3.client("bedrock", region_name=r).get_foundation_model(modelIdentifier=model)
            ok(f"model {model} available")
    except (BotoCoreError, ClientError) as exc:
        problem(f"could not check model access ({type(exc).__name__})")

    try:
        boto3.client("bedrock-agentcore-control", region_name=r).list_agent_runtimes(maxResults=1)
        ok("AgentCore control plane reachable")
    except (BotoCoreError, ClientError) as exc:
        problem(f"AgentCore not usable in {r} ({type(exc).__name__})")

    cfn = boto3.client("cloudformation", region_name=r)
    try:
        cfn.describe_stacks(StackName="CDKToolkit")
        ok("CDK bootstrap stack present (not modified by this project)")
    except ClientError:
        problem("CDK bootstrap missing in this account/Region (bootstrap is an account owner decision)")
    try:
        status = cfn.describe_stacks(StackName=cfg["stackName"])["Stacks"][0]["StackStatus"]
        if status.endswith("FAILED") or status == "ROLLBACK_COMPLETE":
            problem(f"stack exists in state {status}; delete it before deploying")
        else:
            ok(f"stack exists ({status}); deploy will update it")
    except ClientError:
        ok("stack does not exist yet")

    try:
        role = boto3.client("apigateway", region_name=r).get_account().get("cloudwatchRoleArn")
        if role:
            ok("API Gateway account-level CloudWatch Logs role is set")
        else:
            problem(
                "API Gateway has no account-level CloudWatch Logs role in this Region; the stack enables "
                "API logging and would fail. Setting it is an account-level change for the account owner "
                "(OPEN-QUESTIONS.md)."
            )
    except (BotoCoreError, ClientError) as exc:
        problem(f"could not read the API Gateway account settings ({type(exc).__name__})")

    ec2 = boto3.client("ec2", region_name=r)
    quotas = boto3.client("service-quotas", region_name=r)

    def headroom(service: str, code: str, used: int, need: int, what: str) -> None:
        try:
            limit = quotas.get_service_quota(ServiceCode=service, QuotaCode=code)["Quota"]["Value"]
        except ClientError:
            info(f"could not read the {what} quota; {used} in use")
            return
        (ok if used + need <= limit else problem)(f"{what}: {used} in use, quota {int(limit)}, need {need}")

    vpcs = sum(len(p["Vpcs"]) for p in ec2.get_paginator("describe_vpcs").paginate())
    headroom("vpc", "L-F678F1CE", vpcs, 1, "VPCs")
    headroom(
        "ec2",
        "L-0263D0A3",
        len(ec2.describe_addresses()["Addresses"]),
        int(cfg["natGateways"]),
        "Elastic IPs",
    )
    info(
        "check Amazon Bedrock tokens-per-minute and AgentCore quotas in Service Quotas for your expected load"
    )

    print("\nBLOCKED: " + "; ".join(PROBLEMS) if PROBLEMS else "\nPreflight passed.")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    sys.exit(main())
