# NL_control - natural-language smart home control on Amazon Bedrock AgentCore

> **Prototype - not for production use.** This is sample code for demonstration and evaluation in
> non-production accounts. You are responsible for testing, securing and operating anything you deploy from it and
> for its costs; work with your security and legal teams before any further use (see the
> [AWS Shared Responsibility Model](https://aws.amazon.com/compliance/shared-responsibility-model/)). Start with the
> [production hardening checklist](docs/handover.md#production-hardening-checklist).

[中文说明](README.zh-CN.md)

A reference implementation for adding a "natural language -> structured plan -> validation -> dispatch"
path to an existing smart-home platform **without changing its device access, permission system or command
dispatch**. A lightweight model on Amazon Bedrock only interprets intent; deterministic code decides what is
allowed, and the existing control plane executes.

What it shows:
* **Existing control plane stays unchanged.** The agent returns a plan; the orchestrator dispatches through the
  platform's REST API. The agent's only tool (through AgentCore Gateway) is a read-only status query, and a
  default-deny Cedar policy on the gateway enforces that.
* **Context preloading**, so a control request needs a single model call.
* **Hard checks in code**: UUID whitelist, command and parameter ranges, contradictions, action cap, fixed
  fallback for malformed output or model failures.
* **Execution-layer sensitive-action policy** (unlock, disarm) with single-use, user-bound confirmation; voice
  requests are held for confirmation in the app.
* **Ambiguity guard** for same-named devices; **deterministic memory write rules**; **reply text from
  control-plane acceptance results**, with command receipts confirmed asynchronously.
* **Synthetic evaluation set** for all of the above, runnable offline or against a real model.

## Architecture
```
App / voice  ->  orchestrator: session, preload context  ->  AgentCore Runtime (Strands + Amazon Nova Lite)
             <-  reply from accepted results  <-  validate · ambiguity · sensitive-action policy  <-  plan
                                                     |
                                                     v  parallel dispatch via the existing platform API
                                               AWS IoT Core -> devices -> async state back to app
```
Component diagram, request sequence, trust boundaries, design trade-offs and the fact-check table:
[docs/architecture.md](docs/architecture.md). Threat model: [THREAT_MODEL.md](THREAT_MODEL.md).

## Prerequisites and costs
* A **disposable, non-production** AWS account and Region where Amazon Bedrock AgentCore and the configured model
  are available; CDK bootstrap in that account and Region.
* Python 3.12+ and [uv](https://docs.astral.sh/uv/), Node.js 20+, AWS CLI v2. No Docker: the agent is packaged
  as a zip with ARM64 wheels (AgentCore Runtime direct code deployment). Details:
  [docs/deployment-guide.md](docs/deployment-guide.md).
* **Costs.** You are responsible for the costs of what you deploy. Billing dimensions: model tokens
  ([Amazon Bedrock](https://aws.amazon.com/bedrock/pricing/)), Runtime / Memory / Gateway / Identity usage
  ([Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/pricing/)), cache
  ([ElastiCache](https://aws.amazon.com/elasticache/pricing/)), NAT gateway hours and data
  ([Amazon VPC](https://aws.amazon.com/vpc/pricing/)), [AWS Lambda](https://aws.amazon.com/lambda/pricing/),
  [Amazon API Gateway](https://aws.amazon.com/api-gateway/pricing/), [AWS WAF](https://aws.amazon.com/waf/pricing/),
  [AWS IoT Core](https://aws.amazon.com/iot-core/pricing/), [Amazon DynamoDB](https://aws.amazon.com/dynamodb/pricing/),
  [Amazon Cognito](https://aws.amazon.com/cognito/pricing/),
  [AWS Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/) and
  [Amazon CloudWatch](https://aws.amazon.com/cloudwatch/pricing/). No free-tier assumption is made. The NAT gateway
  and the serverless cache bill while the stack exists, so destroy it after testing.

## Deploy
```bash
uv sync --all-groups
make preflight          # read-only: confirms account/Region, tools, model, quotas; stop if the account is not yours
make deploy             # runs preflight first; parameters in config/sandbox.json
make seed               # synthetic demo users and homes
```
Use `CONFIG=config/handover.json` for a non-production account (data stores retained); see
[docs/handover.md](docs/handover.md).

## Use and verify
```bash
uv run python scripts/demo.py say "我要睡觉了"
make e2e                # a healthy stack is not proof the application works; this checks the controls too
```
Guided walkthrough: [docs/demo-script.md](docs/demo-script.md). What was run and the results, with the Region,
model and commit: [VERIFICATION.md](VERIFICATION.md) and [DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md).

## Troubleshooting
* `make preflight` fails on VPC or Elastic IP headroom: choose another Region or free capacity; the stack needs one
  VPC and one Elastic IP per NAT gateway.
* Asset build fails: `uv` must reach PyPI to download ARM64 wheels; a package without an ARM64 wheel cannot be
  used in the agent (see D26 in [DECISIONS.md](DECISIONS.md)).
* Requests return the fallback reply: check the orchestrator log for `agent_invocation_failed` or
  `plan_parse_failed` (raw model output is logged there, never returned to users).

## Security and limitations
See [SECURITY.md](SECURITY.md) (what is deployed, data handling, controls and where they are implemented, known
considerations), [SECURITY-NOTES.md](SECURITY-NOTES.md) (cdk-nag acknowledgements with reasons) and the limits in
[docs/architecture.md](docs/architecture.md#limits-and-known-gaps). Open business questions:
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md). Design decisions: [DECISIONS.md](DECISIONS.md).

## Clean up
```bash
make destroy            # asks for confirmation
make verify-cleanup     # read-only leftover check
```
Not removed automatically: log groups created by services after the stack is deleted, asset objects (agent
package, Lambda code) in the shared CDK bootstrap S3 bucket, and - with the handover
profile - the retained data stores. Do not assume that a completed command removed every artifact; see
[docs/cleanup.md](docs/cleanup.md).

## Repository layout
```
src/core/smarthome_core/   cloud-agnostic domain logic (validation, policy, ambiguity, memory rules, orchestrator)
src/agent/                 Strands agent for AgentCore Runtime (direct code deployment, ARM64)
src/aws/smarthome_aws/     AWS adapters (Valkey with IAM auth, AgentCore Runtime/Memory, platform client, EMF)
src/functions/             Lambda handlers (orchestrator, simulated platform, virtual devices, state sync)
infra/                     AWS CDK app (Python) with cdk-nag and config validation
config/                    parameter files (sandbox, handover)
local/                     in-memory fakes and local runner
scripts/                   preflight, eval, seed, demo, end-to-end test, leftover check
sample-data/               synthetic homes and evaluation cases
tests/                     unit, adapter, config and evaluator tests
docs/                      architecture, deployment, demo, handover, cleanup, prior art
```

## Test locally
No AWS account is needed:
```bash
make lint test eval     # ruff, unit/adapter/config tests, evaluation set with the mock model
make local              # full pipeline with in-memory fakes and a rule-based planner
make synth              # CloudFormation synth with cdk-nag
```

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security
See [SECURITY.md](SECURITY.md) for more information.

## License
This library is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file. Third-party dependencies and
their licenses: [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md).
