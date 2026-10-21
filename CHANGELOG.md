# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-21
### Security
- Skip reasons are fixed codes; model prose never reaches replies, responses or (by default) logs.
- Non-finite numbers in model output are rejected without exceptions; downstream failures (platform, cache,
  runtime, confirmation) return the fixed fallback reply instead of an error; idempotency keys are bound to the
  request content (HTTP 409 on reuse) and claimed atomically.
- Same-name devices need a universal quantifier to act on all of them; room names match by longest name;
  remembered aliases count for ambiguity and cannot be reassigned between devices.
- Device state reports are filtered against the catalog; receipts are bound to their device.
- Trust policies narrowed (runtime `runtime/<prefix_>_agent*`) or added (IoT rule error role, VPC flow-log role);
  workload-identity permissions scoped to the runtime and gateway names; cdk-nag acknowledgements attached per
  construct and built from configuration.
- `mcp` 1.30.0 (GHSA-84m7-p3x7-pcfv), certifi 2026.10.19 (PYSEC-2026-4320); 7-day uv cooldown.

### Changed
- Resource prefix `proto-nl`, stacks `proto-nl-sandbox` / `proto-nl-handover`; platform path `/members/{userId}`.
- Configuration ships as templates (`config/*.example.json`, `make init`); synth needs no AWS credentials.
- Preflight confirms the account by its last 4 digits; `make deploy` refuses deletions/replacements without
  `ALLOW_DESTRUCTIVE=1`; model-invoking commands need `ALLOW_PAID=1`; CDK CLI pinned.
- Memory writes create before deleting and use plain (paginated) listings; used preferences are refreshed.
- Cognito domain prefix no longer contains the account id.
- Runtime log group retention set by the stack; CDK provider logs in a stack-owned group.

### Added
- Cleanup script with two confirmations; leftover check with pagination, more resource classes and
  bootstrap-asset identification by hash; secret-scanner canary; deploy-verify workflow; CODEOWNERS;
  ASH, osv-scanner and format checks in CI; evaluation set version 1.1.0 with a negative control;
  docs/standards-review.md.
- Natural-language control path: preload, single model call on AgentCore Runtime, validation, sensitive-action
  policy, parallel dispatch through the existing platform API, reply from acceptance results.
- Asynchronous state path over AWS IoT Core with command receipts.
- AgentCore Memory with deterministic preference governance; AgentCore Gateway (OpenAPI target, API-key provider)
  with a default-deny Cedar policy engine; AgentCore Identity (OAuth2 M2M); runtime resource policy.
- Synthetic evaluation set (mock and real-model modes), unit, adapter and config tests, end-to-end script,
  preflight and leftover-check scripts; cdk-nag clean synth.
- Agent deployed as an AgentCore Runtime direct code deployment (ARM64 wheels built with uv); no Docker needed.
  The container image (`src/agent/Dockerfile`) remains as an optional path.
