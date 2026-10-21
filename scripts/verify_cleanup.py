"""Read-only leftover check after `make destroy`. Deletes nothing.

    uv run python scripts/verify_cleanup.py --config config/sandbox.json [--regions us-east-1 us-west-2]

Lists anything of this prototype that still exists, by stack name, resource prefix, tag and (for the
shared CDK bootstrap bucket) asset hash. Every list call follows pagination. Tag-index hits are re-checked
against the owning service because the index lags behind deletions. Exits non-zero if anything is left.
Output never contains the account id.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from common import ROOT, region

LEFT: list[str] = []
_ACCOUNT = re.compile(r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])")


def mask(text: str) -> str:
    return _ACCOUNT.sub("111122223333", text)


def found(r: str, kind: str, names) -> None:
    for n in names:
        LEFT.append(f"{r} {kind} {n}")
        print(f"  LEFT  {kind}: {mask(str(n))}")


def items(client, op: str, key: str, **kw) -> Iterator[dict]:
    """Every item of a list/describe call, following the paginator or nextToken/NextToken."""
    if client.can_paginate(op):
        for page in client.get_paginator(op).paginate(**kw):
            yield from page.get(key, [])
        return
    token_key = token = None
    for _ in range(1000):
        args = {**kw, **({token_key: token} if token else {})}
        page = getattr(client, op)(**args)
        yield from page.get(key, [])
        token_key = next((n for n in ("nextToken", "NextToken") if page.get(n)), None)
        if not token_key:
            return
        token = page[token_key]


def safe(r: str, kind: str, fn) -> None:
    try:
        fn()
    except (BotoCoreError, ClientError) as exc:
        # A check that could not run is reported as a leftover: the result would be unproven.
        found(r, f"check failed ({kind})", [type(exc).__name__])


def still_exists(r: str, arn: str) -> bool:
    """Re-check a tag-index hit with the owning service (the index lags behind deletions)."""
    svc = arn.split(":")[2]
    rid = arn.split(":", 5)[5]
    last = rid.split("/")[-1]
    ec2 = boto3.client("ec2", region_name=r)
    try:
        if svc == "cognito-idp":
            boto3.client("cognito-idp", region_name=r).describe_user_pool(UserPoolId=last)
        elif svc == "ec2" and rid.startswith("natgateway/"):
            st = ec2.describe_nat_gateways(NatGatewayIds=[last])["NatGateways"]
            return bool(st) and st[0]["State"] not in ("deleted", "deleting")
        elif svc == "ec2" and rid.startswith("vpc-endpoint/"):
            st = ec2.describe_vpc_endpoints(VpcEndpointIds=[last])["VpcEndpoints"]
            return bool(st) and st[0]["State"].lower() not in ("deleted", "deleting")
        elif svc == "ec2" and rid.startswith("subnet/"):
            ec2.describe_subnets(SubnetIds=[last])
        elif svc == "ec2" and rid.startswith("vpc/"):
            ec2.describe_vpcs(VpcIds=[last])
        elif svc == "ec2" and rid.startswith("vpc-flow-log/"):
            return bool(ec2.describe_flow_logs(FlowLogIds=[last])["FlowLogs"])
        elif svc == "ec2" and rid.startswith("elastic-ip/"):
            ec2.describe_addresses(AllocationIds=[last])
        elif svc == "logs":
            name = rid.removeprefix("log-group:").removesuffix(":*")
            groups = boto3.client("logs", region_name=r).describe_log_groups(logGroupNamePrefix=name)[
                "logGroups"
            ]
            return any(g["logGroupName"] == name for g in groups)
        return True
    except ClientError:
        return False


def check_region(r: str, cfg: dict) -> None:
    p, stack = cfg["resourcePrefix"], cfg["stackName"]
    p_ = p.replace("-", "_")
    print(f"== {r}")

    def cfn():
        c = boto3.client("cloudformation", region_name=r)
        try:
            st = c.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
            found(r, "stack", [f"{stack} ({st})"])
        except ClientError:
            pass

    def agentcore():
        ac = boto3.client("bedrock-agentcore-control", region_name=r)
        found(
            r,
            "agent runtime",
            [
                x["agentRuntimeName"]
                for x in items(ac, "list_agent_runtimes", "agentRuntimes")
                if x["agentRuntimeName"].startswith(p_)
            ],
        )
        found(
            r, "gateway", [x["name"] for x in items(ac, "list_gateways", "items") if x["name"].startswith(p)]
        )
        found(
            r, "memory", [x["id"] for x in items(ac, "list_memories", "memories") if x["id"].startswith(p_)]
        )
        found(
            r,
            "policy engine",
            [
                x["name"]
                for x in items(ac, "list_policy_engines", "policyEngines")
                if x["name"].startswith(p_)
            ],
        )
        found(
            r,
            "api-key provider",
            [
                x["name"]
                for x in items(ac, "list_api_key_credential_providers", "credentialProviders")
                if x["name"].startswith(p)
            ],
        )
        found(
            r,
            "oauth2 provider",
            [
                x["name"]
                for x in items(ac, "list_oauth2_credential_providers", "credentialProviders")
                if x["name"].startswith(p)
            ],
        )
        found(
            r,
            "workload identity",
            [
                x["name"]
                for x in items(ac, "list_workload_identities", "workloadIdentities")
                if x["name"].startswith((p, p_))
            ],
        )

    def cache():
        ec = boto3.client("elasticache", region_name=r)
        found(
            r,
            "serverless cache",
            [
                x["ServerlessCacheName"]
                for x in items(ec, "describe_serverless_caches", "ServerlessCaches")
                if x["ServerlessCacheName"].startswith(p)
            ],
        )
        found(
            r,
            "cache user",
            [x["UserId"] for x in items(ec, "describe_users", "Users") if x["UserId"].startswith(p)],
        )
        found(
            r,
            "cache user group",
            [
                x["UserGroupId"]
                for x in items(ec, "describe_user_groups", "UserGroups")
                if x["UserGroupId"].startswith(p)
            ],
        )

    def iot():
        found(
            r,
            "iot rule",
            [
                x["ruleName"]
                for x in items(boto3.client("iot", region_name=r), "list_topic_rules", "rules")
                if x["ruleName"].startswith(p_)
            ],
        )

    def waf():
        found(
            r,
            "web acl",
            [
                x["Name"]
                for x in items(
                    boto3.client("wafv2", region_name=r), "list_web_acls", "WebACLs", Scope="REGIONAL"
                )
                if x["Name"].startswith(p)
            ],
        )

    def logs():
        lg = boto3.client("logs", region_name=r)
        for prefix in (f"/aws/bedrock-agentcore/runtimes/{p_}", f"/aws/lambda/{stack}", f"/aws/lambda/{p}"):
            found(
                r,
                "log group",
                [
                    g["logGroupName"]
                    for g in items(lg, "describe_log_groups", "logGroups", logGroupNamePrefix=prefix)
                ],
            )
        found(
            r,
            "log group (stack)",
            [
                g["logGroupName"]
                for g in items(lg, "describe_log_groups", "logGroups", logGroupNamePrefix=stack)
            ],
        )

    def data():
        ddb = boto3.client("dynamodb", region_name=r)
        found(
            r, "dynamodb table", [t for t in items(ddb, "list_tables", "TableNames") if t.startswith(stack)]
        )
        sm = boto3.client("secretsmanager", region_name=r)
        found(
            r,
            "secret (incl. scheduled for deletion)",
            [
                s["Name"]
                for s in items(sm, "list_secrets", "SecretList", IncludePlannedDeletion=True)
                if any(
                    t.get("Key") == "aws:cloudformation:stack-name" and t.get("Value") == stack
                    for t in s.get("Tags", [])
                )
            ],
        )
        sqs = boto3.client("sqs", region_name=r)
        found(
            r,
            "sqs queue",
            [u.rsplit("/", 1)[-1] for u in items(sqs, "list_queues", "QueueUrls", QueueNamePrefix=stack)],
        )

    def compute():
        lam = boto3.client("lambda", region_name=r)
        found(
            r,
            "lambda function",
            [
                f["FunctionName"]
                for f in items(lam, "list_functions", "Functions")
                if f["FunctionName"].startswith(stack)
            ],
        )
        found(
            r,
            "lambda layer",
            [
                x["LayerName"]
                for x in items(lam, "list_layers", "Layers")
                if (x.get("LatestMatchingVersion") or {}).get("Description", "").startswith("smarthome_core")
            ],
        )
        apigw = boto3.client("apigateway", region_name=r)
        found(
            r,
            "rest api",
            [a["name"] for a in items(apigw, "get_rest_apis", "items") if a["name"].startswith(p)],
        )
        cog = boto3.client("cognito-idp", region_name=r)
        found(
            r,
            "user pool",
            [
                u["Name"]
                for u in items(cog, "list_user_pools", "UserPools", MaxResults=60)
                if u["Name"].startswith(p)
            ],
        )
        cw = boto3.client("cloudwatch", region_name=r)
        found(
            r,
            "dashboard",
            [
                d["DashboardName"]
                for d in items(cw, "list_dashboards", "DashboardEntries", DashboardNamePrefix=p)
            ],
        )
        found(
            r,
            "alarm",
            [a["AlarmName"] for a in items(cw, "describe_alarms", "MetricAlarms", AlarmNamePrefix=stack)],
        )

    def tagged():
        tag = cfg.get("tags", {}).get("Project")
        if not tag:
            return
        tagging = boto3.client("resourcegroupstaggingapi", region_name=r)
        arns = [
            m["ResourceARN"]
            for m in items(
                tagging,
                "get_resources",
                "ResourceTagMappingList",
                TagFilters=[{"Key": "Project", "Values": [tag]}],
            )
        ]
        found(r, "tagged resource", [a for a in arns if still_exists(r, a)])

    def bootstrap():
        from bootstrap_assets import project_objects

        account = boto3.client("sts").get_caller_identity()["Account"]
        hits, _ = project_objects(r, account)
        found(r, "bootstrap asset object version", [f"{h['Key']} ({h['VersionId']})" for h in hits])

    for kind, fn in (
        ("cloudformation", cfn),
        ("agentcore", agentcore),
        ("elasticache", cache),
        ("iot", iot),
        ("wafv2", waf),
        ("logs", logs),
        ("data", data),
        ("compute/api", compute),
        ("tags", tagged),
        ("bootstrap assets", bootstrap),
    ):
        safe(r, kind, fn)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sandbox.json")
    ap.add_argument("--regions", nargs="*")
    a = ap.parse_args()
    cfg = json.loads((ROOT / a.config).read_text(encoding="utf-8"))
    for r in a.regions or [region()]:
        check_region(r, cfg)

    def roles():
        iam = boto3.client("iam")
        found(
            "global",
            "iam role",
            [
                x["RoleName"]
                for x in items(iam, "list_roles", "Roles")
                if x["RoleName"].startswith(cfg["stackName"])
            ],
        )
        found(
            "global",
            "iam policy",
            [
                x["PolicyName"]
                for x in items(iam, "list_policies", "Policies", Scope="Local")
                if x["PolicyName"].startswith(cfg["stackName"])
            ],
        )

    safe("global", "iam", roles)
    print("\nLEFTOVERS: " + str(len(LEFT)) if LEFT else "\nNo leftovers found.")
    return 1 if LEFT else 0


if __name__ == "__main__":
    sys.exit(main())
