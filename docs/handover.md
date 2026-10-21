# Handover

This repository is a **prototype** to be deployed in a **non-production** account. It does not
touch production data and needs no production permissions.

## What to hand over
* The repository, the template `config/handover.example.json` (data stores retained on stack deletion; `make init`
  copies it to the git-ignored `config/handover.json`), and this document.
* The synthetic sample data only. No real users, homes or devices are included.

## Deploying in a non-production account
1. Review `config/handover.json`: `resourcePrefix`, `stackName`, Region (`AWS_REGION`), `modelId`, `natGateways`,
   `logRetentionDays`.
2. Deploy with the commands in the [README](../README.md#deploy), passing `CONFIG=config/handover.json`.
3. `removalPolicy: retain` keeps DynamoDB tables, the ElastiCache cache and the AgentCore Memory when the stack
   is deleted. Delete them manually when no longer needed (see [cleanup.md](cleanup.md)).

## Integrating with the real platform
| Prototype part | Replace with |
|---|---|
| `src/functions/platform_api` + DynamoDB tables | The platform's existing REST API. Provide its OpenAPI description for the three operations (list devices, device status, send command) and set its URL in the gateway target and orchestrator. |
| API-key auth to the platform | The platform's real auth: an AgentCore Identity OAuth2 or API-key credential provider for the gateway, and the same credential for the orchestrator. |
| `src/functions/virtual_device` | Real devices on AWS IoT Core. |
| `src/core/smarthome_core/catalog.py` | Generated from the platform's capability (thing) model. |
| `policy.py` sensitive-action table | The platform's list of sensitive actions (see OPEN-QUESTIONS.md). |
| Demo users in Cognito | The platform's identity provider; the API authoriser and `runtimeUserId` must use the platform's user id. |
| Shared M2M identity to the gateway | Per-user tokens or a gateway interceptor that binds `userId` to the caller (THREAT_MODEL.md T3). |

## Production hardening checklist
- [ ] Replace AWS managed Lambda policies with inline least-privilege policies.
- [ ] Automatic rotation for every secret; move the platform credential to the platform's own mechanism.
- [ ] MFA / step-up authentication for confirmations of sensitive actions; Cognito threat protection.
- [ ] Device certificates and IoT policies binding each speaker and device to its own topics.
- [ ] Make the platform API private (VPC-only) where the gateway supports private endpoints for the target.
- [ ] Per-user model token quotas and alarms; WAF tuning.
- [ ] CloudWatch Transaction Search and tracing for runtime, memory and gateway (account owner decision).
- [ ] Cross-request conflict detection for contradictory commands sent in quick succession.
- [ ] Load test against the acceptance-latency target with production-like device counts; cache sizing limits.
- [ ] Backup and retention policy for the command log and memory; data-subject deletion for memory records.
- [ ] Multi-AZ NAT, alarms on DLQ depth, Lambda errors and throttles; on-call runbooks.
- [ ] Expand the evaluation set with real (anonymised) utterances and run it on every prompt or model change.
- [ ] Keep dependency scanning (osv-scanner) and secret scanning in CI; run ASH with all scanners before release.
- [ ] Per-user identity from the agent to the gateway, or a Cedar / interceptor rule on `userId`.
- [ ] Alarms on `PreloadFailure`, `AgentInvocationFailure`, `UnhandledFailure`, `HandlerFailure`, `CacheFailure`.
- [ ] Vended log delivery (APPLICATION_LOGS, TRACES) for Memory and Gateway into stack-owned log groups once the
      account owner accepts the account-level CloudWatch Logs resource policy it needs (D19).
- [ ] Customer managed KMS keys where required (DynamoDB, SQS, Secrets Manager, logs); Lambda reserved
      concurrency; review the checkov hardening findings listed in VERIFICATION.md.
