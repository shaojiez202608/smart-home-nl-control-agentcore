"""Read-only leftover check after `cdk destroy`. Deletes nothing.

    uv run python scripts/verify_cleanup.py --config config/sandbox.json [--regions us-west-2 us-east-1]

Lists anything of this prototype that still exists, by stack name, resource prefix and tag. Tag-index hits are
re-checked against the owning service because the index lags behind deletions. Exits non-zero if anything is left.
CDK bootstrap assets (shared S3 bucket) are reported separately for manual review.
"""

from __future__ import annotations

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError
from common import ROOT, region

LEFT: list[str] = []


def found(r: str, kind: str, names) -> None:
    for n in names:
        LEFT.append(f"{r} {kind} {n}")
        print(f"  LEFT  {kind}: {n}")


def still_exists(r: str, arn: str) -> bool:
    """Re-check a tag-index hit with the owning service (the index lags behind deletions)."""
    svc = arn.split(":")[2]
    rid = arn.split(":", 5)[5]
    try:
        if svc == "cognito-idp":
            boto3.client("cognito-idp", region_name=r).describe_user_pool(UserPoolId=rid.split("/")[-1])
        elif svc == "ec2" and rid.startswith("natgateway/"):
            st = boto3.client("ec2", region_name=r).describe_nat_gateways(NatGatewayIds=[rid.split("/")[-1]])
            return st["NatGateways"][0]["State"] not in ("deleted", "deleting")
        elif svc == "ec2" and rid.startswith("vpc-endpoint/"):
            boto3.client("ec2", region_name=r).describe_vpc_endpoints(VpcEndpointIds=[rid.split("/")[-1]])
        elif svc == "ec2" and rid.startswith("vpc-flow-log/"):
            fl = boto3.client("ec2", region_name=r).describe_flow_logs(FlowLogIds=[rid.split("/")[-1]])
            return bool(fl["FlowLogs"])
        return True
    except ClientError:
        return False


def check_region(r: str, cfg: dict) -> None:
    p, p_ = cfg["resourcePrefix"], cfg["resourcePrefix"].replace("-", "_")
    print(f"== {r}")
    cfn = boto3.client("cloudformation", region_name=r)
    try:
        st = cfn.describe_stacks(StackName=cfg["stackName"])["Stacks"][0]["StackStatus"]
        found(r, "stack", [f"{cfg['stackName']} ({st})"])
    except ClientError:
        pass
    ac = boto3.client("bedrock-agentcore-control", region_name=r)
    found(
        r,
        "agent runtime",
        [
            x["agentRuntimeName"]
            for x in ac.list_agent_runtimes()["agentRuntimes"]
            if x["agentRuntimeName"].startswith(p_)
        ],
    )
    found(r, "gateway", [x["name"] for x in ac.list_gateways()["items"] if x["name"].startswith(p)])
    found(r, "memory", [x["id"] for x in ac.list_memories()["memories"] if x["id"].startswith(p_)])
    found(
        r,
        "policy engine",
        [x["name"] for x in ac.list_policy_engines().get("policyEngines", []) if x["name"].startswith(p_)],
    )
    found(
        r,
        "api-key provider",
        [
            x["name"]
            for x in ac.list_api_key_credential_providers()["credentialProviders"]
            if x["name"].startswith(p)
        ],
    )
    found(
        r,
        "oauth2 provider",
        [
            x["name"]
            for x in ac.list_oauth2_credential_providers()["credentialProviders"]
            if x["name"].startswith(p)
        ],
    )
    ec = boto3.client("elasticache", region_name=r)
    found(
        r,
        "serverless cache",
        [
            x["ServerlessCacheName"]
            for x in ec.describe_serverless_caches()["ServerlessCaches"]
            if x["ServerlessCacheName"].startswith(p)
        ],
    )
    found(r, "cache user", [x["UserId"] for x in ec.describe_users()["Users"] if x["UserId"].startswith(p)])
    found(
        r,
        "iot rule",
        [
            x["ruleName"]
            for x in boto3.client("iot", region_name=r).list_topic_rules()["rules"]
            if x["ruleName"].startswith(p_)
        ],
    )
    found(
        r,
        "web acl",
        [
            x["Name"]
            for x in boto3.client("wafv2", region_name=r).list_web_acls(Scope="REGIONAL")["WebACLs"]
            if x["Name"].startswith(p)
        ],
    )
    logs = boto3.client("logs", region_name=r)
    for prefix in (f"/aws/bedrock-agentcore/runtimes/{p_}", f"/aws/lambda/{cfg['stackName']}"):
        found(
            r,
            "log group",
            [g["logGroupName"] for g in logs.describe_log_groups(logGroupNamePrefix=prefix)["logGroups"]],
        )
    tag = cfg.get("tags", {}).get("Project")
    if tag:
        tagging = boto3.client("resourcegroupstaggingapi", region_name=r)
        arns = [
            m["ResourceARN"]
            for m in tagging.get_resources(TagFilters=[{"Key": "Project", "Values": [tag]}])[
                "ResourceTagMappingList"
            ]
        ]
        found(
            r,
            "tagged resource",
            [a.replace(a.split(":")[4], "<ACCOUNT_ID>") for a in arns if still_exists(r, a)],
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sandbox.json")
    ap.add_argument("--regions", nargs="*")
    a = ap.parse_args()
    cfg = json.loads((ROOT / a.config).read_text(encoding="utf-8"))
    for r in a.regions or [region()]:
        check_region(r, cfg)
    roles = [
        x["RoleName"]
        for x in boto3.client("iam").list_roles()["Roles"]
        if x["RoleName"].startswith(cfg["stackName"])
    ]
    found("global", "iam role", roles)
    print(
        "\nNot checked automatically (shared): objects in the CDK bootstrap S3 bucket - see docs/cleanup.md."
    )
    print("\nLEFTOVERS: " + str(len(LEFT)) if LEFT else "\nNo leftovers found.")
    return 1 if LEFT else 0


if __name__ == "__main__":
    sys.exit(main())
