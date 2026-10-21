# Design decisions

Each entry records the choice, the alternatives considered, and why the more conservative option was taken.

## D1. The model plans; it never executes
* **Choice:** the agent returns a structured plan. The orchestrator validates it and calls the existing
  platform API to dispatch. The agent's only tool is a read-only status query.
* **Alternative:** give the agent a `controlDevice` tool (common in samples).
* **Why:** keeps authorisation and dispatch in the existing control plane, makes every safety check
  deterministic and testable, and means a prompt injection can at most produce a plan that code then rejects.
  The gateway still exposes `controlDevice` (the platform API is wrapped as-is) but the agent is not given it.

## D2. Device context is preloaded by the orchestrator
* **Choice:** the orchestrator reads the user's devices, command sets and latest state from the cache (falling
  back to the platform API) and sends them with the request, so the control path needs a single model call.
* **Alternative:** let the agent call `getUserDevices` through the gateway.
* **Why:** removes a model round trip from the critical path; the same optimisation works without a managed
  runtime. Verified locally: a control request completes in one event-loop cycle.

## D3. Sensitive actions require a second confirmation on every channel
* **Choice:** `lock.unlock` and `security_system.disarm` are always held; confirmation is only accepted on the
  authenticated app API, with a single-use token bound to the user and a short TTL. Voice requests are held and
  listed in the app.
* **Alternatives:** deny on voice entirely; allow on app without confirmation; block per device type.
* **Why:** per-action policy keeps "lock the door at bedtime" working while making unlock impossible without an
  explicit human step. A voice channel cannot present a second factor, so it never confirms.

## D4. A failed or foreign confirmation consumes the token
* **Choice:** the token is popped atomically before the user check.
* **Why:** prevents token probing; the legitimate user re-issues the request, which is cheap.

## D5. Preference writes are decided by code, not by the model's label
* **Choice:** a taste parameter is written only if its value appears in the utterance; values the model marks
  as coming from preferences are never written back.
* **Alternative:** trust the model's `params_source="user"`.
* **Why:** in testing with a real model, stated values were labelled as defaults, so the label was unreliable
  in both directions. A deterministic rule is auditable.

## D6. Reply text for control requests is built from dispatch results
* **Why:** the model's `message` is written before anything is dispatched; it cannot know what the control
  plane accepted. "已下发" means accepted by the control service; execution is confirmed by receipts.

## D7. Runtime invoked with IAM and `runtimeUserId`
* **Choice:** the orchestrator calls `InvokeAgentRuntime` with SigV4 and passes the authenticated user's id
  (Cognito `sub`, or the registered owner of a speaker) as `runtimeUserId`.
* **Alternative:** JWT inbound authorisation on the runtime.
* **Why:** a single runtime serves both the app channel (user JWT) and the voice channel (no user JWT). The
  AWS documentation states the user id is not verified by AgentCore, so it is always derived server-side from
  an authenticated principal, and `InvokeAgentRuntimeForUser` is granted only to the orchestrator role.

## D8. Gateway uses Cognito M2M inbound auth and an API-key credential provider outbound
* **Why:** shows the AgentCore Identity credential chain without changing the platform API. User-level
  authorisation stays in the platform (membership check) and the user id is injected by agent code, not chosen
  by the model.

## D9. ElastiCache Serverless (Valkey) with IAM authentication
* **Alternative:** password auth from Secrets Manager, or no cache.
* **Why:** no static cache password exists anywhere; access string limited to the key prefixes and commands
  the code uses; ingress only from the Lambda security group.

## D10. Single stack, removal policy as a parameter
* **Why:** atomic deploy and destroy for sandbox testing. `config/sandbox.json` destroys everything;
  `config/handover.json` retains data stores (tables, cache, memory).

## D11. Account-level settings are not changed
* **Choice:** CloudWatch Transaction Search (needed for AgentCore spans in CloudWatch) is not enabled by the
  stack or by any script; the API Gateway account logging role is used as already configured.
* **Why:** both are account-wide settings outside the scope of a prototype stack. See OPEN-QUESTIONS.md.

## D12. Default model
* **Choice:** `us.amazon.nova-lite-v1:0` (cross-region inference profile), configurable.
* **Why:** a lightweight model fits shallow intent matching with structured output. The inference profile
  `us.amazon.nova-2-lite-v1:0` was also listed by `ListInferenceProfiles` in us-east-1 during testing
  (2026-10-21) and can be selected in the config file; it was not evaluated in this round.

## D13. Python everywhere, arm64
* **Why:** one language for core, Lambdas, agent and CDK; Lambdas run on arm64, and the agent package is built
  from ARM64 (aarch64) wheels for the direct-code-deployment runtime (D26).

## D14. Prompt layout: one data block, no system-prompt caching
* **Tried:** stable device data in a cacheable system prompt (with a cache point), state in the user message.
* **Observed:** cache hits confirmed, no clear latency reduction, plan quality on the evaluation set regressed.
* **Choice:** devices with their state in one data block in the user message; system prompt holds rules only.

## D15. Nova Micro is not the default
* **Observed:** locally comparable on the evaluation set, but on the deployed runtime many requests failed with
  "Model produced invalid sequence as part of ToolUse".
* **Choice:** keep Amazon Nova Lite as the default; `modelId` stays configurable. The orchestrator answers any
  runtime or model failure with the fixed fallback instead of an error status.

## D16. Memory reads off the waiting path
* **Choice:** the orchestrator preloads preferences from the cache (filled from AgentCore Memory on a miss and
  invalidated on every preference write) and sends them with the request; recent turns are kept in the runtime
  session's microVM and read from Memory only for a new microVM. Memory remains the source of truth.

## D17. Newest record wins for preferences
* **Why:** `ListMemoryRecords` is eventually consistent, so an overwrite implemented as delete-then-create can
  leave a superseded record. Reads keep only the newest record per device and attribute; each write deletes
  every superseded record it can see.

## D18. Narrow, explicit normalisation of model output
* **Choice:** two rewrites only: digit-only strings for integer parameters become integers; `turnOn` with a
  single value parameter (brightness, colour temperature, target temperature) becomes the matching `set*`
  action. Everything else that does not match the catalogue is rejected.

## D19. Second review against recent AgentCore samples
A second review compared the prototype with recent public AgentCore samples (aws-samples and awslabs). Changes
adopted from it are D20-D25; items considered and not adopted are listed at the end of this entry.
* Not adopted at the time: Runtime direct code deployment (adopted later, see D26); CDK L2 constructs (the OpenAPI target with an API-key provider still needs L1); vended log delivery
  for Memory and Gateway (it creates CloudWatch Logs resource policies at account level that the stack would not
  own); AgentCore Evaluations and online evaluation (need CloudWatch Transaction Search, an account setting); IAM
  ABAC on Memory actor/namespace (the orchestrator writes for many users, so per-user session tags do not apply).

## D20. Runtime resource policy
Only the orchestrator role may call `InvokeAgentRuntime` / `InvokeAgentRuntimeForUser`; every other principal is
explicitly denied, whatever its identity-based permissions. This bounds the trust placed in `runtimeUserId` (D25).

## D21. Default-deny Cedar policy engine on the gateway
The gateway still exposes all three platform operations (the API is wrapped as-is), but the policy engine,
attached in `ENFORCE` mode (`gatewayPolicyMode`), permits only `queryDeviceStatus` and `getUserDevices` for
tokens from this stack's user pool. `controlDevice` is denied at the gateway, so device state can only change
through the orchestrator's validated path even if the agent were changed to call it.

## D22. Trust policies with source ARN
Every service role whose service documents the confused-deputy keys carries both `aws:SourceAccount` and an
`ArnLike aws:SourceArn` narrowed to this stack's resource: runtime `runtime/<prefix_>_agent*`, gateway
`gateway/<prefix>-gateway*`, IoT rule error role `rule/<prefix_>_*`, flow-log role `vpc-flow-log/*` (a dedicated
role instead of the CDK default, which has no conditions). Lambda execution roles have none (no documented
keys); see the table in SECURITY-NOTES.md.

## D23. Narrower identity permissions for the runtime and gateway
The runtime no longer holds `GetWorkloadAccessToken*`: the workload access token is delivered to the container
by the runtime. It keeps `GetResourceOauth2Token` on the workload-identity directory, its own workload identity
(`workload-identity/<prefix_>_agent*`), the token vault and the one OAuth2 provider. The gateway's
`GetWorkloadAccessToken` / `GetResourceApiKey` is scoped to `workload-identity/<prefix>-gateway*`. The runtime
role keeps `GetSecretValue` on the M2M client secret: removing it was tried in this round and the deployment
check failed (`GetResourceOauth2Token` reads an `EXTERNAL` client secret with the caller's permissions).

## D24. Explicit runtime lifecycle
`idleRuntimeSessionTimeout` equals the orchestrator's session idle TTL (`sessionIdleSeconds`), so a session id
expires in the cache when its microVM (and the in-process conversation history) is gone; `maxLifetime` is a
parameter.

## D25. Batch-call failures; runtimeUserId kept, agent-side cross-check removed
`BatchCreateMemoryRecords` failures (`failedRecords`) raise; partial delete failures are logged. The agent tags
traces with user and session.
* **runtimeUserId:** the orchestrator passes the verified user id (Cognito `sub`, or the speaker's registered
  owner) as `runtimeUserId`. Removing it was tried in this round and failed in the deployment check: for SigV4
  callers the runtime hands the container a workload access token only when a runtime user id is given, so
  the agent could not obtain its M2M token for the gateway (the SDK fell back to `CreateWorkloadIdentity`,
  which was denied). It is therefore required, not optional. `InvokeAgentRuntimeForUser` is granted only to the
  orchestrator role, and the runtime resource policy denies every other principal (D20).
* **Cross-check removed:** the earlier agent code compared the payload user with a runtime user header; the
  runtime does not pass that header to the container (only allow-listed custom headers and the workload token
  reach it), so the check never ran. It was removed rather than kept as an inert control. The payload user is
  trusted because only the orchestrator can invoke the runtime and it takes the id from verified identity.

## D26. Direct code deployment for the agent (no Docker)
* **Choice:** the agent ships as an AgentCore Runtime direct code deployment: `infra/smarthome_infra/build.py`
  installs ARM64 wheels for Python 3.13 with `uv pip install --python-platform aarch64-manylinux2014
  --only-binary :all:`, adds `agent_app/` and a root `main.py`, and CDK uploads the directory as an S3 asset
  (`runtime: PYTHON_3_13`, entry point `opentelemetry-instrument main.py`). The runtime role may read only that
  one object.
* **Why:** deploying needs no Docker or other container runtime, only uv and Node.js; the build is the same on
  any OS. Memory clients and the identity-token function are created per call, because a code-deployed
  runtime may start from a snapshot and keep credentials captured at start-up.
* **Alternative:** the container image (`src/agent/Dockerfile`, kept as an optional path for teams that need
  system packages). It needs a local runtime able to build `linux/arm64` images and leaves images in the CDK
  bootstrap ECR repository.

## D27. Downstream failures: fixed reply, never an unhandled 5xx
* **Choice:** inside the orchestrator every failure on the request path (preload, cache, runtime, model,
  confirmation, anything unexpected) returns `type: "error"` with a fixed reply and an EMF metric. In the Lambda
  handler, the reply routes (`POST /v1/commands`, `POST /v1/confirmations`) answer **HTTP 200** with the fixed
  reply on any remaining exception; the read-only routes answer **HTTP 503** with a fixed body. An idempotency
  key reused for a different request is **HTTP 409**.
* **Why:** the app always gets a reply it can show for user-facing requests; read routes have no reply to show,
  and 503 tells a client to retry. Failure metrics (`PreloadFailure`, `UnhandledFailure`, `HandlerFailure`, ...)
  keep outages visible.

## D28. Skip reasons are codes
* **Choice:** `skipped[].reason` is one of `already_in_state`, `sensitive_action`, `not_needed`, `other`; any other
  model output becomes `other`. The reply shows fixed text per code (none for `other`); the response carries the
  code only.
* **Why:** the reply must never contain model-written prose (FR-10). A code still lets the app explain the most
  common case (a lock already locked).

## D29. mcp pinned to 1.30.0
* **Choice:** `mcp>=1.30.0,<2.0` in `pyproject.toml` and `mcp==1.30.0` in `src/agent/requirements.txt`.
* **Why:** GHSA-84m7-p3x7-pcfv (High) affects mcp 1.8.0-1.29.1 and 2.0.0-2.1.1 and is fixed in 1.30.0 and 2.2.0
  (OSV record, checked 2026-10-21). `strands-agents` 1.61.1 (latest) declares `mcp<2.2`, so 2.2.0 cannot be
  installed without overriding its declared range; 1.30.0 is the fixed version inside that range. The agent uses
  only the MCP client; the advisory concerns Streamable HTTP servers. Dependabot ignores mcp major updates until
  strands-agents accepts mcp>=2.2.

## D30. Same-name devices and remembered aliases
* **Choice:** a plan that targets every device of a same-name group is accepted only when the utterance contains
  a universal quantifier (都, 全部, 所有, 全都, 每个, 每一个); a room name disambiguates only as the longest matched
  room name; remembered aliases count as labels for ambiguity detection; an alias already remembered for another
  device is refused with a question.
* **Why:** FR-8 says an unresolvable reference must be confirmed, not guessed. Refusing a conflicting alias is
  simpler and more conservative than silently moving it; the user can choose another name.
* **Limits:** "都" can appear in unrelated phrasing; the quantifier list is deliberately short.

## D31. Preference expiry means "not used"
* **Choice:** records still expire `preferenceTtlDays` after they were written, but a dispatched action that used
  a remembered value rewrites the record when it is older than 7 days (`REFRESH_AFTER_DAYS`), which resets its
  expiry. Stated values must be next to their unit or after a setting phrase.
* **Why:** FR-9 asks to age out preferences that are no longer triggered; refreshing on every use would add a
  Memory write to most requests.

## D32. Idempotency bound to the request
* **Choice:** the idempotency entry stores a hash of channel and utterance and is claimed with `SET NX`; a reused
  key with another request is a conflict (HTTP 409); a retry while the first attempt runs gets a fixed "in
  progress" reply; failed requests release the key.
* **Limits:** if a request fails after dispatching, a retry can dispatch again.

## D33. Account-agnostic synth, templates for configuration, guarded commands
* **Choice:** the stack passes no account to `cdk.Environment`, so synth needs no credentials or context lookups
  (CI) and no account id is written to `cdk.json` or configuration. Parameter files are templates
  (`config/*.example.json`, copied by `make init`). `make preflight` shows the last 4 digits of the account and
  requires confirmation; `make deploy` refuses deletions/replacements without `ALLOW_DESTRUCTIVE=1`; commands that
  call the model need `ALLOW_PAID=1`; the CDK CLI version is pinned.
* **Why:** standards decisions 5 and 6. The two AZs now come from `Fn::GetAZs` at deploy time.

## D34. Resource prefix and Cognito domain
* **Choice:** `resourcePrefix` `proto-nl`, stacks `proto-nl-sandbox` / `proto-nl-handover`, tag `Project=proto-nl`;
  the stack name must start with the prefix (cleanup matches on it). The Cognito domain prefix uses the first
  block of the stack UUID instead of the account id, so the public hosted domain does not reveal the account.
  The platform path segment `users` became `members` (`/members/{userId}/devices`, event topic `members/<id>/events`).

## D35. Bootstrap assets identified by asset hash
* **Choice:** `scripts/bootstrap_assets.py` lists this project's objects in the shared CDK bootstrap bucket: a
  template counts only if sha256(content) equals its key and its Description is this stack's; code assets count
  only if such a template references them from this project's own resources. CDK's generic provider packages are
  never listed. `make destroy` deletes the listed versions after a second confirmation; `make verify-cleanup`
  fails while any remain. Unidentified objects are never deleted.

## D36. History rewrite once
The history was rewritten once with git-filter-repo before this round (author identity, Conventional Commit
subjects, removal of internal names and earlier prefixes). It is not rewritten again, so commit hashes in
VERIFICATION.md and DEPLOYMENT-LOG.md stay valid. Side effect: the text replacement renamed the API path segment `users` to
`members` in all historical snapshots but not the CDK path segment `users` and the IAM topic `users/*/events`
in `stack.py`, so commits before 0.1.0 are not deployable as-is; the first 0.1.0 commit made them consistent.
