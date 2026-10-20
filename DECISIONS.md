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
* **Why:** a lightweight model fits shallow intent matching with structured output; Nova 2 Lite is available
  in the test region and can be selected in the config file.

## D13. Python everywhere, arm64
* **Why:** one language for core, Lambdas, agent and CDK; Lambdas run on arm64, the agent container is ARM64
  (required by AgentCore Runtime).

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
A second review against recent public AgentCore samples. Changes
adopted from it are D20-D25; items considered and not adopted are listed at the end of this entry.
* Not adopted at the time: Runtime direct code deployment (adopted later, see D26); CDK L2 constructs (the OpenAPI target with an API-key provider still needs L1); vended log delivery
  for Memory and Gateway (it creates CloudWatch Logs resource policies at account level that the stack would not
  own); AgentCore Evaluations and online evaluation (need CloudWatch Transaction Search, an account setting); IAM
  ABAC on Memory actor/namespace (the orchestrator writes for many users, so per-user session tags do not apply).

## D20. Runtime resource policy
Only the orchestrator role may call `InvokeAgentRuntime` / `InvokeAgentRuntimeForUser`; every other principal is
explicitly denied, whatever its identity-based permissions. This bounds the trust placed in `runtimeUserId`.

## D21. Default-deny Cedar policy engine on the gateway
The gateway still exposes all three platform operations (the API is wrapped as-is), but the policy engine,
attached in `ENFORCE` mode (`gatewayPolicyMode`), permits only `queryDeviceStatus` and `getUserDevices` for
tokens from this stack's user pool. `controlDevice` is denied at the gateway, so device state can only change
through the orchestrator's validated path even if the agent were changed to call it.

## D22. Trust policies with source ARN
The gateway role's trust policy adds `aws:SourceArn` (this stack's gateway name pattern) to `aws:SourceAccount`;
the runtime role already had both.

## D23. Narrower identity permissions for the runtime
The runtime no longer holds `GetWorkloadAccessToken*`: the workload access token is delivered to the container
by the runtime. It keeps `GetResourceOauth2Token` on the workload-identity directory, the token vault and the one
OAuth2 provider.

## D24. Explicit runtime lifecycle
`idleRuntimeSessionTimeout` equals the orchestrator's session idle TTL (`sessionIdleSeconds`), so a session id
expires in the cache when its microVM (and the in-process conversation history) is gone; `maxLifetime` is a
parameter.

## D25. Batch-call failures and identity cross-check in the agent
`BatchCreateMemoryRecords` failures (`failedRecords`) raise; partial delete failures are logged. The agent tags
traces with user and session, and rejects a payload whose user differs from the runtime user header when the
runtime exposes that header to the container.

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
