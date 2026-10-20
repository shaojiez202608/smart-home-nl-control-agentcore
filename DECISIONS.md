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
