# Security notes

This is a prototype for non-production accounts. This file lists the controls that are implemented, the
service-role trust policies, the deliberate gaps, and every cdk-nag acknowledgement with its scope and reason.

## Implemented controls

| Area | Control |
|---|---|
| Entry points | App API requires a Cognito ID token (user pool authoriser); request bodies validated against JSON schemas; stage throttling; AWS WAF (managed common and known-bad-input rule groups, per-IP rate rule). The simulated platform API requires an API key and a usage plan, behind the same WAF. |
| Runtime access | Resource policy on the AgentCore Runtime: only the orchestrator role may call `InvokeAgentRuntime`; all other principals are explicitly denied. No principal holds `InvokeAgentRuntimeForUser`, and the orchestrator does not send `runtimeUserId` (DECISIONS.md D25). |
| Gateway tools | Default-deny Cedar policy engine in `ENFORCE` mode: only the read-only tools are permitted, for tokens issued by this stack's user pool; the device-control tool is denied at the gateway. |
| IAM | One role per function; resource-scoped statements (tables, topics under the configured topic prefix, the one runtime, the one memory, the one cache and cache user). No IAM users, no access keys. Workload-identity permissions are scoped to this stack's runtime name (`<prefix_>_agent*`) and gateway name (`<prefix>-gateway*`); the runtime's token-vault read is limited to the one OAuth2 provider; the runtime has no access to the M2M client secret. |
| Network | Lambdas that need the cache run in private subnets; the Lambda security group has no ingress and egress only to the cache (6379) and HTTPS (443). The cache security group accepts 6379 only from the Lambda security group. No `0.0.0.0/0` ingress anywhere. VPC flow logs enabled. DynamoDB gateway endpoint. |
| Data | DynamoDB with encryption at rest and point-in-time recovery; ElastiCache Serverless (encryption in transit and at rest, IAM authentication, restricted access string); Secrets Manager for the API key and the M2M client secret; SQS dead-letter queue with SSE and TLS-only policy; log groups with retention. No S3 buckets are created by the stack (CDK bootstrap assets only); the runtime role can read only the one agent package object. |
| Application | UUID whitelist, command and parameter validation (non-finite numbers rejected), contradiction handling, action cap, per-action sensitive-action policy with single-use confirmation tokens, same-name and remembered-alias ambiguity guard, fixed fallback for malformed output and downstream failures, reply text from dispatch results and fixed reason codes, deterministic memory writes, device reports filtered against the catalog, idempotency keys bound to the request content. |
| Observability | X-Ray tracing on Lambdas and API stages; access logs; EMF metrics for latency segments, hard-check counters, failures, tool errors and receipt latency; acceptance-latency alarm. |

## Service-role trust policies

Checked against each service's documentation on 2026-10-21. Conditions are added where the service documents
`aws:SourceAccount` / `aws:SourceArn` for its `sts:AssumeRole` call.

| Role | Service principal | Conditions | Source |
|---|---|---|---|
| `RuntimeRole` | `bedrock-agentcore.amazonaws.com` | `aws:SourceAccount` = this account; `aws:SourceArn` ArnLike `...:runtime/<prefix_>_agent*` | [AgentCore Runtime trust policy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html#runtime-permissions-trust-policy) |
| `GatewayRole` | `bedrock-agentcore.amazonaws.com` | `aws:SourceAccount`; `aws:SourceArn` ArnLike `...:gateway/<prefix>-gateway*` | same service, gateway resource type |
| `IotRuleErrorRole` | `iot.amazonaws.com` | `aws:SourceAccount`; `aws:SourceArn` ArnLike `arn:aws:iot:<REGION>:<ACCOUNT>:rule/<prefix_>_*` | [AWS IoT cross-service confused deputy prevention](https://docs.aws.amazon.com/iot/latest/developerguide/cross-service-confused-deputy-prevention.html) |
| `FlowLogRole` | `vpc-flow-logs.amazonaws.com` | `aws:SourceAccount`; `aws:SourceArn` ArnLike `arn:aws:ec2:<REGION>:<ACCOUNT>:vpc-flow-log/*` (the flow-log id is not known before the role exists) | [IAM role for publishing flow logs](https://docs.aws.amazon.com/vpc/latest/userguide/flow-logs-iam-role.html) |
| Lambda execution roles (4 functions, CDK provider function) | `lambda.amazonaws.com` | none | The [Lambda execution role documentation](https://docs.aws.amazon.com/lambda/latest/dg/lambda-intro-execution-role.html) gives no confused-deputy condition keys for the execution-role trust policy; Lambda assumes the role only for functions that reference it, and creating such a function requires `iam:PassRole` in this account. Not added. |

Lambda invoke permissions for the IoT rules carry `SourceArn` (the rule) and `SourceAccount`.

## Known gaps (prototype scope)
* No automatic secret rotation (API key, M2M client secret).
* No MFA or Cognito threat protection; demo users only.
* The simulated platform API is internet-facing (needed for the AgentCore Gateway OpenAPI target); in a real
  integration it is the platform's existing API with its own authentication.
* The gateway trusts the `userId` tool argument: all agent sessions share one M2M identity (THREAT_MODEL.md T3).
* The voice channel is exercised with developer IAM credentials; no device certificates or IoT policies are
  provisioned.
* AWS managed policies `AWSLambdaBasicExecutionRole` / `AWSLambdaVPCAccessExecutionRole` are used.
* AgentCore spans in CloudWatch require Transaction Search, an account-level setting that this stack does not
  change.
* checkov findings on the synthesized template (KMS customer managed keys, Lambda concurrency limits, API
  caching, ...) are classified in VERIFICATION.md; they are hardening items, not acknowledged in code.

## cdk-nag acknowledgements (AwsSolutions pack)

Defined in `infra/smarthome_infra/nag.py`. Each acknowledgement is attached to the construct listed under
"Scope" (paths relative to the stack); nothing is acknowledged stack-wide, so a new finding anywhere else fails
`cdk synth`. Rule ids that contain configured values are built from the configuration (`topicPrefix`,
`resourcePrefix`, `modelId`); `make synth` also synthesizes a variant with other prefixes to prove it.

| Rule | Scope | Reason |
|---|---|---|
| IAM4 AWSLambdaBasicExecutionRole | `PlatformApiFn/ServiceRole`, `VirtualDeviceFn/ServiceRole`, `StateSyncFn/ServiceRole`, `OrchestratorFn/ServiceRole`, `AWS679f53fac002430cb0da5b7982bd2287/ServiceRole` (CDK provider) | AWS managed policy limited to CloudWatch Logs writes; replace with inline policies in production. |
| IAM4 AWSLambdaVPCAccessExecutionRole | `StateSyncFn/ServiceRole`, `OrchestratorFn/ServiceRole` | Required for ENI management of VPC-attached functions. |
| IAM5 `Resource::*` | `*Fn/ServiceRole/DefaultPolicy` (4 functions) | Generated by Lambda active tracing: X-Ray writes have no resource-level permissions. |
| IAM5 `Resource::*` | `IotDataEndpoint/CustomResourcePolicy` | `iot:DescribeEndpoint` has no resource-level permissions. |
| IAM5 runtime log group `...-DEFAULT:*` | `RuntimeLogRetention/CustomResourcePolicy` | The `:*` form of the one runtime log group's ARN, used to set its retention and delete it on destroy. |
| IAM5 `Resource::*` | `RuntimePolicy` | X-Ray writes, `logs:DescribeLogGroups`, `cloudwatch:PutMetricData` (namespace condition). |
| IAM5 table `/index/*` | `PlatformApiFn/...DefaultPolicy`, `StateSyncFn/...DefaultPolicy` | Generated by `Table.grant_*`; the table's own indexes only. |
| IAM5 `topic/<topicPrefix>/devices/*/cmd` | `PlatformApiFn/ServiceRole/DefaultPolicy` | Wildcard is the device-id segment. |
| IAM5 `topic/<topicPrefix>/devices/*/state` | `VirtualDeviceFn/ServiceRole/DefaultPolicy` | Wildcard is the device-id segment. |
| IAM5 `topic/<topicPrefix>/members/*/events` | `StateSyncFn/ServiceRole/DefaultPolicy` | Wildcard is the user-id segment. |
| IAM5 `topic/<topicPrefix>/speakers/*/reply` | `OrchestratorFn/ServiceRole/DefaultPolicy` | Wildcard is the speaker-id segment. |
| IAM5 `<Runtime>/runtime-endpoint/*` | `OrchestratorFn/ServiceRole/DefaultPolicy` | Endpoints of this one runtime. |
| IAM5 `bedrock:*::foundation-model/<base model>` | `RuntimePolicy` | Cross-Region inference profiles route to the same model in several Regions. |
| IAM5 `workload-identity/<prefix_>_agent*` | `RuntimePolicy` | The runtime's workload identity carries a service-generated suffix. |
| IAM5 runtime log groups `/aws/bedrock-agentcore/runtimes/*` | `RuntimePolicy` | Log groups are named by the service after the runtime id. |
| IAM5 `workload-identity/<prefix>-gateway*` | `GatewayRole/DefaultPolicy` | The gateway's workload identity carries a service-generated suffix. |
| IAM5 `gateway/<prefix>-gateway*` | `GatewayRole/DefaultPolicy` | The gateway ARN has a service-generated suffix and the role must exist before the gateway (policy-engine authorisation). |
| SMG4 | `PlatformApiKeySecret`, `GatewayClientSecret` | Rotation would need the API Gateway key / Cognito client updated in step; on the production checklist. |
| COG2 | `UserPool` | Demo users only; MFA on the production checklist. |
| COG8 | `UserPool` | Plus-tier threat protection out of prototype scope. |
| APIG4, COG4 | `PlatformApi` (its methods) | Simulated existing platform protected with API key, usage plan and WAF; user-level authorisation inside the platform. |

The earlier stack-wide `L1` acknowledgement was removed: no L1 finding exists (verified by synthesizing without
any acknowledgement).
