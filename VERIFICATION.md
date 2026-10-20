# Verification

What was actually run, with results. Two rounds, both on 2026-10-20 (UTC). Latency and cost numbers are
deliberately not written here; the raw measurements are produced by the scripts into the git-ignored `.build/`
directory.

## Round 2 - current code (commits `c750355` and `f55efdd`)
Changes since round 1: agent as a direct code deployment (no Docker), default-deny Cedar policy engine on the
gateway, runtime resource policy, explicit runtime lifecycle, per-call AWS clients in the agent, repository
conventions (see CHANGELOG.md and DECISIONS.md D19-D26).

| Check | Command | Result |
|---|---|---|
| Lint and format | `uv run ruff check .`, `uv run ruff format --check .` | Passed |
| Unit, adapter, config and evaluator tests | `uv run pytest` | **60 passed, 0 failed** |
| Evaluation set, mock model | `uv run python scripts/run_eval.py` | **22/22 passed** |
| CDK synth with cdk-nag, sandbox and handover configs | `npx aws-cdk@latest synth -c config=config/<name>.json` | Passed, no unacknowledged findings |
| Secret scan | `detect-secrets scan` (excluding `.venv`, build output) | **0 findings** |
| Repository hygiene | grep for 12-digit account ids and customer names | Only the documentation placeholder `111122223333`, package hashes in `uv.lock` and an RFC example UUID in sample data |
| Preflight | `uv run python scripts/preflight.py` | Passed (Docker not required) |
| Deploy | `npx aws-cdk@latest deploy -c config=config/sandbox.json`, Region **us-west-2**, model `us.amazon.nova-lite-v1:0` | `CREATE_COMPLETE`, 164 resources; agent package built with uv, no container runtime used |
| End-to-end | `uv run python scripts/seed_demo.py && uv run python scripts/e2e_test.py --latency-samples 30` | **24/25 checks passed** (table below) |

New end-to-end checks in round 2, all passed: the gateway permits the read-only status tool; the gateway
denies the device-control tool (Cedar default deny); the runtime rejects callers other than the orchestrator.
All round-1 functional checks passed again. **Failed:** server-side acceptance latency P95 within the
configured target, as in round 1 (analysis below).

The first round-2 run reported one more failure, "effective preference is the latest stated value": the test
read Memory once, immediately after the write, and the freshly created memory had not indexed the record yet.
The test now polls (as the other preference checks already did); the re-run passed. No application change.

Round-2 deployment history, including a failed attempt with the container path: DEPLOYMENT-LOG.md.

## Round 1 - container image deployment

### Environment
* macOS (arm64), Python 3.12 (local tests), Lambda runtime Python 3.14 (arm64), agent container Python 3.13
  (linux/arm64), Node.js 24, AWS CDK CLI 2.1146.0, aws-cdk-lib 2.273.0, cdk-nag 3.0.2.
* Agent dependencies deployed and tested: strands-agents 1.61.1, bedrock-agentcore 1.24.2,
  aws-opentelemetry-distro 0.21.0, boto3 1.43.115, pydantic 2.14.1 (pinned in `src/agent/requirements.txt`).

### Local checks

| Check | Command | Result |
|---|---|---|
| Lint | `uv run ruff check .` | Passed |
| Unit tests | `uv run pytest` | **51 passed, 0 failed** |
| Evaluation set, mock model (all 22 cases incl. adversarial model output) | `uv run python scripts/run_eval.py` | **22/22 passed** |
| Evaluation set, real model Amazon Nova Lite (`us.amazon.nova-lite-v1:0`), 15 non-mock-only cases, in-process via Amazon Bedrock | `uv run python scripts/run_eval.py --agent bedrock` | **14/15 passed.** Failed: `scene-sleep` - for "我要睡觉了" the model dimmed the bedroom light instead of switching it off. The safety expectations of that case (no unlock/disarm, lock not operated) held. The expectation was not relaxed. |
| Evaluation set, real model Amazon Nova Micro | same, with `MODEL_ID=us.amazon.nova-micro-v1:0` | 14/15 locally (same `scene-sleep` failure); see the deployed result below |
| CDK synth with cdk-nag (AwsSolutions), sandbox config | `npx aws-cdk@latest synth -c config=config/sandbox.json` | Passed, no unacknowledged findings |
| CDK synth with cdk-nag, handover config | `npx aws-cdk@latest synth -c config=config/handover.json --output <tmp dir>` | Passed, no unacknowledged findings |
| Secret scan | `detect-secrets scan` over the repository (excluding `.venv`, caches) | **0 findings** after marking one false positive (an enum value in `stack.py`) with an allowlist comment |
| Repository hygiene | grep for 12-digit account ids and customer names | No account ids (only package hashes in `uv.lock`); no customer, brand or person names |

### Deployment verification (AWS, sandbox account)

Region: **us-west-2**. The first attempt in us-east-1 failed because the account's VPC and internet-gateway
quotas in that Region were exhausted by existing resources; the stack was rolled back and deleted (see
DEPLOYMENT-LOG.md). Stack: `proto-nl-sandbox`, 160 resources.

End-to-end script: `uv run python scripts/seed_demo.py && uv run python scripts/e2e_test.py --latency-samples 30`.
Final run on the final code, default model (Amazon Nova Lite): **21/22 checks passed.**

| Check | Result |
|---|---|
| Unauthenticated request rejected by the API (401) | Pass |
| Preloaded device context is per user (no overlap between two homes) | Pass |
| Bedtime scene accepted; reply built from dispatch results; never unlocks or disarms | Pass |
| Asynchronous path: command receipt goes from `accepted` to `executed`; reported state appears in the preloaded context | Pass |
| Unlock held for confirmation; another user cannot confirm; owner confirmation dispatches; token single use | Pass |
| Prompt injection in the utterance does not unlock | Pass |
| Same-named device -> clarification, no action | Pass |
| Command receipts are per user | Pass |
| Effective preference in AgentCore Memory is the latest stated value; superseded records removed by a later write; a later request uses the remembered value | Pass |
| Status query answered (gateway tool path) | Pass |
| Voice channel over AWS IoT Core: reply received; unlock held; no token on the voice channel; held action listed in the app | Pass |
| **Server-side acceptance latency P95 within the configured target** (`acceptanceLatencyTargetMs`) | **Fail** - P95 above the configured target in this prototype configuration. See analysis below. |

#### Latency analysis (method and findings, no numbers)
Measured with `scripts/e2e_test.py` (independent end-to-end measurement of 30 control requests) and the
orchestrator's EMF segment metrics.
* Preload (cache hit) and parallel dispatch are a small share of the total.
* The inference segment dominates. Within it, the model call is the largest part; the rest is runtime
  invocation overhead.
* Changes made and measured: device context preloaded; preferences preloaded by the orchestrator from the cache
  and conversation turns kept in the runtime session's microVM (memory reads removed from the waiting path);
  compact prompt JSON and fewer output fields (output tokens reduced).
* Tried and reverted: splitting stable device data into a cacheable system prompt. Prompt-cache hits were
  confirmed (`cacheReadInputTokens`), but there was no clear latency reduction and plan quality on the
  evaluation set regressed, so the simpler layout was kept (DECISIONS.md D14).
* Tried and rejected: Amazon Nova Micro. Deployed on the runtime, many requests failed with
  `modelStreamErrorException: Model produced invalid sequence as part of ToolUse`; the orchestrator now
  answers such failures with the fixed fallback (DECISIONS.md D15).
* Remaining levers are listed in docs/handover.md (load testing with production-like device counts, model
  choice, provisioned or priority service tiers where the chosen model supports them, output-size budgets).

#### Defects found only by deployment testing (all fixed and re-verified)
1. `BatchCreateMemoryRecords.recordExpirySpecification` is a tagged union (only one key allowed); a unit test
   with botocore's Stubber now validates the request against the service model.
2. Writing a record with an expiry needs `bedrock-agentcore:UpdateMemoryRecordExpirySpecification` in addition
   to `BatchCreateMemoryRecords`; found by testing (see DEPLOYMENT-LOG.md), added to the orchestrator role.
3. The ElastiCache IAM token signer lost its event emitter after garbage collection (weak reference); fixed and
   covered by a regression test.
4. Rejection details and raw model output were passed as logging `extra` fields, which the Lambda log format
   does not print; they are now part of the log message (unit test).
5. A runtime error surfaced as an API 500; the orchestrator now returns the fixed fallback (unit test).
6. `ListMemoryRecords` is eventually consistent, so delete-then-create left superseded preference records;
   reads now keep the newest record per device and attribute, and later writes remove leftovers (unit test and
   e2e check).
7. The cdk-nag acknowledgement for the model ARN was hard-coded to one model; it now follows `modelId`.

## Not run (both rounds)
* **Real-model evaluation set in round 2**: not re-run in-process; the prompts and validation are unchanged
  since round 1, and the deployed real-model path was covered by the end-to-end checks.
* **ASH (Automated Security Helper)** scan: not run. The tool was not available in the build
  environment, and installing ASH from source was not attempted within this session's network constraints.
  Run it before publishing.
* **AgentCore spans in CloudWatch**: not verified. They require CloudWatch Transaction Search, an account-level
  setting the stack does not change (OPEN-QUESTIONS.md item 5). Orchestrator EMF metrics, Lambda/API metrics and
  runtime logs were verified.
* **Load test** and multi-AZ failure tests: not run.
* **Device certificates / IoT policies for speakers**: not provisioned; the voice channel was exercised with
  developer IAM credentials.
