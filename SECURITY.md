# Security

## Reporting a security issue
If you discover a potential security issue in this project, notify AWS Security through the
[vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/) or by email to
aws-security@amazon.com. Please do **not** create a public GitHub issue.

## Scope
This is sample code for non-production use. It is not production-ready. You are responsible for testing,
securing and operating anything you deploy from it; work with your security and legal teams before any further
use. See the [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/).

## What this prototype deploys
One AWS CloudFormation stack (AWS CDK) in one Region: a VPC with private subnets and a NAT gateway; two Amazon API
Gateway REST APIs (app API behind an Amazon Cognito authorizer, simulated platform API behind an API key) with AWS
WAF; AWS Lambda functions; Amazon ElastiCache Serverless (Valkey); Amazon DynamoDB tables; AWS IoT Core topic rules;
Amazon Cognito user pool; Amazon Bedrock AgentCore Runtime, Memory, Gateway (with a Cedar policy engine) and Identity
credential providers; AWS Secrets Manager secrets; Amazon CloudWatch logs, dashboard and alarm.

## Data handling
* Only synthetic sample data (`sample-data/`). Demo user passwords are generated at seed time and stored only in
  the git-ignored `.demo/` directory.
* Utterances and plans are processed by the configured Amazon Bedrock model. Conversation turns and preference
  records are stored in AgentCore Memory with configured expiry; command receipts in DynamoDB with a TTL.
* Raw model output is written to troubleshooting logs only (log retention is a parameter).

## Controls in place
| Control | Implemented in |
|---|---|
| Authentication on the app API; request validation; throttling; AWS WAF | `infra/smarthome_infra/stack.py` (`_rest_api`, `_waf`) |
| UUID whitelist, command/parameter validation (non-finite numbers rejected), contradictions, action cap | `src/core/smarthome_core/validation.py`, `catalog.py`, `models.py` |
| Reply text from dispatch results and fixed skip-reason codes, never model prose | `src/core/smarthome_core/messages.py` |
| Downstream failures return a fixed fallback with a metric (never an unhandled 5xx) | `src/core/smarthome_core/orchestrator.py`, `src/functions/orchestrator/handler.py` |
| Device reports filtered against the catalog; receipts bound to their device | `src/core/smarthome_core/catalog.py`, `src/functions/state_sync/handler.py` |
| Idempotency keys bound to the request content, claimed atomically | `src/core/smarthome_core/orchestrator.py`, `src/aws/smarthome_aws/cache.py` |
| Sensitive actions (unlock, disarm) held for single-use, user-bound confirmation | `src/core/smarthome_core/policy.py`, `orchestrator.py` |
| Same-name and remembered-alias ambiguity guard | `src/core/smarthome_core/ambiguity.py`, `memory_policy.py` |
| Deterministic memory write rules | `src/core/smarthome_core/memory_policy.py`, `src/aws/smarthome_aws/memory_store.py` |
| Runtime resource policy (orchestrator only) | `stack.py` (`RuntimeResourcePolicy`) |
| Default-deny Cedar policy engine on the gateway (read-only tools only) | `stack.py` (`GatewayPolicyEngine`, `ReadOnlyToolsPolicy`) |
| Least-privilege IAM; service-role trust policies with `aws:SourceAccount` and `aws:SourceArn` where the service supports them | `stack.py`; trust-policy table in SECURITY-NOTES.md; acknowledgements per construct in `infra/smarthome_infra/nag.py` |
| Preflight account confirmation, destructive-change guard, paid-command switch | `scripts/preflight.py`, `scripts/deploy.sh`, `scripts/e2e_test.py`, `scripts/run_eval.py` |
| Cache with IAM authentication and TLS; private subnets; no inbound from the internet | `stack.py`, `src/aws/smarthome_aws/cache.py` |
| Secrets in AWS Secrets Manager; no static credentials in code | `stack.py`, `src/aws/smarthome_aws/platform_client.py` |
| cdk-nag (AwsSolutions) on every synth; secret scanning (history + canary) and dependency scanning in CI | `infra/app.py`, `.github/workflows/ci.yml`, `.gitleaks.toml`, `scripts/secret_canary.sh` |

Details and every cdk-nag acknowledgement: [SECURITY-NOTES.md](SECURITY-NOTES.md). Threat model:
[THREAT_MODEL.md](THREAT_MODEL.md).

## Known considerations
* The simulated platform API is internet-facing (API key + WAF) so that the gateway's OpenAPI target can reach it.
* The voice channel is exercised with developer IAM credentials; no device certificates are provisioned.
* No automatic secret rotation; no MFA for demo users.
* AgentCore spans in CloudWatch require CloudWatch Transaction Search, which this stack does not enable.
* The agent's gateway identity is shared by all sessions; the platform trusts the `userId` tool argument for
  the read-only status tool (THREAT_MODEL.md T3).
* `mcp` is pinned to 1.30.0 because `strands-agents` 1.61.1 requires `mcp<2.2`; the agent uses the MCP client
  only (the fixed advisory GHSA-84m7-p3x7-pcfv concerns Streamable HTTP servers).

## Production hardening
Summary (full checklist in [docs/handover.md](docs/handover.md#production-hardening-checklist)):
1. Replace the simulated platform with the real platform API and its authentication; remove the public endpoint.
2. Per-user identity to the gateway (or an interceptor binding `userId` to the token); keep Cedar default-deny.
3. MFA and threat protection on the user pool; step-up authentication for confirmations.
4. Secret rotation for the platform API key and the M2M client secret; customer managed KMS keys where required.
5. Device certificates and IoT policies for speakers and devices instead of developer credentials.
6. Inline least-privilege policies instead of the AWS managed Lambda policies; Lambda concurrency limits.
7. Alarms on the failure metrics (`PreloadFailure`, `AgentInvocationFailure`, `UnhandledFailure`,
   `HandlerFailure`), per-user model-token quotas, and an approved threat model.

## Clean up
`make destroy` (two confirmations) then `make verify-cleanup` in every Region you used; see
[docs/cleanup.md](docs/cleanup.md) for resources that are not removed automatically.
