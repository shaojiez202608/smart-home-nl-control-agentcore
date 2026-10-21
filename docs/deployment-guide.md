# Deployment guide

> Prototype for non-production accounts only.

## Prerequisites

| Tool | Notes |
|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Local tests, CDK app, Lambda layer and agent package build (ARM64 wheels; no Docker needed) |
| Node.js 20+ | CDK CLI via `npx aws-cdk@2.1146.0` (pinned in the Makefile as `CDK_VERSION`) |
| AWS CLI v2 and credentials for a **non-production** account | `aws sts get-caller-identity` must succeed |
| CDK bootstrap in the target account and Region | Done by the account owner (`cdk bootstrap aws://111122223333/<REGION>`); this project never modifies the CDKToolkit stack |
| Model access | The configured Amazon Bedrock model (default Amazon Nova Lite) must be usable in the Region |
| API Gateway CloudWatch logging role | Stage access/execution logs need the account-level API Gateway CloudWatch role to exist; the stack does not change account settings |

Region: AgentCore must be available in the Region (see the fact-check table in [architecture.md](architecture.md)).
The default is the Region of your shell environment, falling back to `us-east-1`.

## Deploying
The deployment commands are in the [README](../README.md#deploy) only. `make deploy` runs the read-only preflight
(account confirmation by its last 4 digits, Region, tools, model, AgentCore, bootstrap, API Gateway logging role,
quotas), shows `cdk diff`, refuses deletions or replacements without `ALLOW_DESTRUCTIVE=1`, deploys, and keeps the
deployed asset manifest in `.deploy/` for cleanup.

`cdk synth` and `cdk deploy` run the cdk-nag AwsSolutions pack; the build fails on any unacknowledged finding.
Synth needs no AWS credentials: the stack is account-agnostic and the AZs are resolved at deploy time.

## Parameters (`config/*.example.json` -> `config/*.json`)

`make init` copies each template to a git-ignored `config/<name>.json`; edit the copy. Validation runs at synth
time (`infra/smarthome_infra/config.py`) and fails with every problem listed.

| Field | Meaning |
|---|---|
| `stackName` | CloudFormation stack name; must start with `resourcePrefix`. |
| `resourcePrefix` | Prefix for named resources (cache, user pool, gateway, runtime, memory, rules). Lowercase letters, digits, `-`. |
| `removalPolicy` | `destroy` (sandbox: everything is deleted with the stack) or `retain` (handover: tables, cache and memory are kept). |
| `modelId` | Bedrock model or inference profile id used by the agent (for example `us.amazon.nova-lite-v1:0`). |
| `modelMaxTokens` | Output token limit per model call. |
| `topicPrefix` | AWS IoT Core topic prefix for device, speaker and user-event topics. |
| `logRetentionDays` | Retention for all log groups created by the stack (1, 3, 5, 7, 14, 30, 90). |
| `memoryEventExpiryDays` | Expiry of short-term conversation events in AgentCore Memory. |
| `preferenceTtlDays` | Expiry of long-term preference and alias records. |
| `acceptanceLatencyTargetMs` | Target for acceptance latency P95; used by the alarm, dashboard annotation and e2e check. |
| `apiThrottle.rateLimit` / `burstLimit` | App API stage throttling; the platform API and WAF rate rule are derived from it. |
| `natGateways` | NAT gateways for the private subnets (1 for sandbox, 2 for availability). |
| `enableWaf` | Associate an AWS WAF web ACL with both APIs. |
| `tags` | Applied to every taggable resource; `Project` is required. Sandbox default: `Project=proto-nl`, `Purpose=prototype`, `Teardown=after-test`. |

## Outputs
`AppApiUrl`, `UserPoolId`, `AppClientId`, table names, `AgentRuntimeArn`, `GatewayUrl`, `MemoryId`,
`TopicPrefix`, `IotDataEndpoint`, `PlatformApiUrl`. The scripts read them from the stack; you do not need to copy them.

## Local mode (no AWS)

```bash
make test
make eval                                   # mock model, then the negative control
make local
ALLOW_PAID=1 uv run python -m local.run_local --agent bedrock "把客厅那个灯调暗点"   # real model, in-process
```

## Verify a deployment
`ALLOW_PAID=1 make verify` (see the README). See [demo-script.md](demo-script.md) for a guided walkthrough.
