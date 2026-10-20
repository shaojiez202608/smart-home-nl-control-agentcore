# Handover

This repository is a **prototype** to be deployed in a customer's **non-production** account. It does not
touch production data and needs no production permissions.

## What to hand over
* The repository, `config/handover.json` (data stores retained on stack deletion), and this document.
* The synthetic sample data only. No real users, homes or devices are included.

## Deploying in a non-production account
1. Review `config/handover.json`: `resourcePrefix`, Region, `modelId`, `natGateways`, `logRetentionDays`.
2. Follow [deployment-guide.md](deployment-guide.md) with `-c config=config/handover.json`.
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
- [ ] Pin and scan container and Python dependencies in CI; sign images.
