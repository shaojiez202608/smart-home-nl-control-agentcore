# Deployment guide

> Prototype for non-production accounts only.

## Prerequisites

| Tool | Notes |
|---|---|
| Python 3.12+ and [uv](https://docs.astral.sh/uv/) | Local tests, CDK app, Lambda layer build |
| Node.js 20+ | CDK CLI via `npx aws-cdk@latest` |
| Docker (or a compatible runtime) able to build `linux/arm64` images | Agent container for AgentCore Runtime |
| AWS CLI v2 and credentials for a **non-production** account | `aws sts get-caller-identity` must succeed |
| CDK bootstrap in the target account and Region | `npx aws-cdk@latest bootstrap aws://<ACCOUNT_ID>/<REGION>` |
| Model access | The configured Amazon Bedrock model (default Amazon Nova Lite) must be usable in the Region |
| API Gateway CloudWatch logging role | Stage access/execution logs need the account-level API Gateway CloudWatch role to exist; the stack does not change account settings |

Region: AgentCore must be available in the Region (see the fact-check table in [architecture.md](architecture.md)).
The default is the Region of your shell environment, falling back to `us-east-1`.

## One-command deploy

```bash
uv sync --all-groups
npx aws-cdk@latest deploy -c config=config/sandbox.json --require-approval never
uv run python scripts/seed_demo.py --config config/sandbox.json
```

`cdk synth` and `cdk deploy` run the cdk-nag AwsSolutions pack; the build fails on any unacknowledged finding.

## Parameters (`config/*.json`)

| Field | Meaning |
|---|---|
| `stackName` | CloudFormation stack name. |
| `resourcePrefix` | Prefix for named resources (cache, user pool, gateway, runtime, memory, rules). Lowercase letters, digits, `-`. |
| `removalPolicy` | `destroy` (sandbox: everything is deleted with the stack) or `retain` (handover: tables, cache and memory are kept). |
| `modelId` | Bedrock model or inference profile id used by the agent. |
| `modelMaxTokens` | Output token limit per model call. |
| `topicPrefix` | AWS IoT Core topic prefix for device, speaker and user-event topics. |
| `logRetentionDays` | Retention for all log groups created by the stack (1, 3, 5, 7, 14, 30, 90). |
| `memoryEventExpiryDays` | Expiry of short-term conversation events in AgentCore Memory. |
| `preferenceTtlDays` | Expiry of long-term preference and alias records. |
| `acceptanceLatencyTargetMs` | Target for acceptance latency P95; used by the alarm, dashboard annotation and e2e check. |
| `apiThrottle.rateLimit` / `burstLimit` | App API stage throttling; the platform API and WAF rate rule are derived from it. |
| `natGateways` | NAT gateways for the private subnets (1 for sandbox, 2 for availability). |
| `enableWaf` | Associate an AWS WAF web ACL with both APIs. |
| `tags` | Applied to every taggable resource. Sandbox default: `Project`, `Purpose=prototype`, `Teardown=after-test`. |

## Outputs
`AppApiUrl`, `UserPoolId`, `AppClientId`, table names, `AgentRuntimeArn`, `GatewayUrl`, `MemoryId`,
`TopicPrefix`, `IotDataEndpoint`, `PlatformApiUrl`. The scripts read them from the stack; you do not need to copy them.

## Local mode (no AWS)

```bash
uv run pytest
uv run python scripts/run_eval.py                 # deterministic mock model
uv run python -m local.run_local "我要睡觉了" "开锁"
uv run python -m local.run_local --agent bedrock "把客厅那个灯调暗点"   # real model, in-process
```

## Verify a deployment
```bash
uv run python scripts/e2e_test.py --config config/sandbox.json
```
See [demo-script.md](demo-script.md) for a guided walkthrough.
