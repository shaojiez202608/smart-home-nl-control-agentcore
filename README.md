# NL_control - natural-language smart home control on Amazon Bedrock AgentCore

> **Prototype - not for production use.** Deploy only in non-production accounts. See the
> [production hardening checklist](docs/handover.md#production-hardening-checklist) before any further use.

[中文说明](README.zh-CN.md)

A reference implementation for adding a "natural language -> structured plan -> validation -> dispatch"
path to an existing smart-home platform **without changing its device access, permission system or command
dispatch**. A lightweight model on Amazon Bedrock only interprets intent; deterministic code decides what is
allowed, and the existing control plane executes.

```
App / voice  ->  orchestrator: session, preload context  ->  AgentCore Runtime (Strands + Amazon Nova Lite)
             <-  reply from accepted results  <-  validate · ambiguity · sensitive-action policy  <-  plan
                                                     |
                                                     v  parallel dispatch via the existing platform API
                                               AWS IoT Core -> devices -> async state back to app
```

## What it shows
* **Existing control plane stays unchanged.** The agent returns a plan; the orchestrator dispatches through the
  platform's REST API. The agent's only tool (through AgentCore Gateway) is a read-only status query.
* **Context preloading**: device lists, command sets and state are sent with the request, so a control request
  needs a single model call.
* **Hard checks in code**: UUID whitelist, command and parameter ranges, contradictions, action cap, fixed
  fallback for malformed output.
* **Execution-layer sensitive-action policy** (per action: unlock, disarm) with single-use, user-bound
  confirmation; voice requests are held for confirmation in the app.
* **Ambiguity guard** for same-named devices; app names and aliases are authoritative.
* **Memory write governance** on AgentCore Memory: only stated preferences, overwrite per device and attribute,
  expiry.
* **Reply text from control-plane acceptance results**, with command receipts confirmed asynchronously.
* **Synthetic evaluation set** for all of the above, runnable offline or against a real model.

## Quick start

```bash
uv sync --all-groups
uv run pytest                               # unit tests
uv run python scripts/run_eval.py           # evaluation set, mock model, no AWS needed
uv run python -m local.run_local "我要睡觉了" "帮我把前门锁打开"

# Deploy to a non-production account (Docker, Node.js and CDK bootstrap required)
npx aws-cdk@latest deploy -c config=config/sandbox.json --require-approval never
uv run python scripts/seed_demo.py
uv run python scripts/e2e_test.py
npx aws-cdk@latest destroy -c config=config/sandbox.json --force
```

## Documentation
| Document | Content |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Diagrams, request path, hard checks, memory rules, observability, limits, fact check |
| [docs/deployment-guide.md](docs/deployment-guide.md) | Prerequisites, one-command deploy, every parameter |
| [docs/demo-script.md](docs/demo-script.md) | Guided demo |
| [docs/handover.md](docs/handover.md) | Integration with a real platform, production checklist |
| [docs/cleanup.md](docs/cleanup.md) | Destroy and verify no leftovers |
| [docs/prior-art.md](docs/prior-art.md) | Related assets and how this one differs |
| [DECISIONS.md](DECISIONS.md) / [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | Design decisions; open business questions |
| [SECURITY-NOTES.md](SECURITY-NOTES.md) / [THREAT_MODEL.md](THREAT_MODEL.md) | Controls, cdk-nag acknowledgements, threat model |
| [VERIFICATION.md](VERIFICATION.md) / [DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md) | What was run and the results |

## Repository layout
```
src/core/smarthome_core/   cloud-agnostic domain logic (validation, policy, ambiguity, memory rules, orchestrator)
src/agent/                 Strands agent for AgentCore Runtime (ARM64 container)
src/aws/smarthome_aws/     AWS adapters (Valkey with IAM auth, AgentCore Runtime/Memory, platform client, EMF)
src/functions/             Lambda handlers (orchestrator, simulated platform, virtual devices, state sync)
infra/                     AWS CDK app (Python) with cdk-nag
config/                    parameter files (sandbox, handover)
local/                     in-memory fakes and local runner
scripts/                   eval, seed, demo, end-to-end test
sample-data/               synthetic homes and evaluation cases
tests/                     unit tests
```

## Costs
Billing dimensions: Amazon Bedrock model tokens, AgentCore Runtime / Memory / Gateway usage, ElastiCache
Serverless, NAT gateway, Lambda, API Gateway, AWS WAF, AWS IoT Core messages, DynamoDB, CloudWatch. See the
official pricing pages, for example [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and
[Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/).

## Security
See [SECURITY.md](SECURITY.md) for reporting issues.

## License
MIT-0. See [LICENSE](LICENSE).
