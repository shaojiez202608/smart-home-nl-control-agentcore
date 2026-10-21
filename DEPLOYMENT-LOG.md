# Deployment log

Account: disposable sandbox account (shown as `111122223333`; preflight confirmed it by its last 4 digits).
Credentials from the shell environment. Every resource carried the tags `Project=proto-nl`, `Purpose=prototype`,
`Teardown=after-test`. Only this project's `proto-nl` resources and this project's objects in the shared CDK
bootstrap buckets were changed; the CDKToolkit stack and other stacks in the account were not touched.
Times are UTC, 2026-10-21. All 12-digit numbers were replaced before writing this file.

## Round 3 - release 0.1.0, us-east-1

### Cycle 1 - first deployment and fixes (stack `proto-nl-sandbox`)
| Time | Commit | Event |
|---|---|---|
| 01:14-01:25 | `18a3493` | `make deploy`: preflight passed (account confirmed, Elastic IP quota reported below current usage, see `18a3493`); `cdk diff` showed a new stack; `CREATE_COMPLETE`. Seed. |
| 01:26-01:30 | `18a3493` | `ALLOW_PAID=1 make verify`: 24/29. Failures: preference records not found or not superseded (filtered listings lagged), status tool failed (`CreateWorkloadIdentity` denied: no workload token without `runtimeUserId`), latency target. |
| 01:42 | `e374910` | Preflight for the update blocked on the stack's own VPC (headroom check fixed). |
| 01:43-01:47 | `e374910` | `make deploy` stopped at the destructive-change guard (Lambda layer replacement, exit 2); diff reviewed, re-run with `ALLOW_DESTRUCTIVE=1`: update complete. |
| 01:48-01:53 | `e374910` | e2e 25/29: status tool still failing (same cause), superseded-record check, latency. |
| 01:58-02:05 | `544c800` | `runtimeUserId` restored; update (guard, then `ALLOW_DESTRUCTIVE=1` for the layer). |
| 02:06-02:10 | `544c800` | e2e 25/29: status tool now failed on `GetResourceOauth2Token` AccessDenied for the M2M client secret; superseded-record check (listing lag); latency. |
| 02:12-02:16 | `74c8792` | `GetSecretValue` on the M2M secret restored for the runtime; e2e waits for the listing; update (no replacement). |
| 02:16-02:20 | `74c8792` | e2e **28/29**: only the latency target failed. |
| 02:21-02:48 | `74c8792` | `make destroy` (`CONFIRM_STACK`, `CONFIRM_DATA=yes`): stack deleted; 2 CDK provider log groups deleted; 53 bootstrap object versions deleted (below). |
| 02:49 | `74c8792` | `make verify-cleanup` us-east-1 + us-west-2: **1 leftover** - log group `proto-nl-sandbox-CustomResourceProviderLogs...`, created again at 02:42 by the provider function while it handled a custom-resource delete. Deleted by name with `aws logs delete-log-group`; root cause fixed in `a8faf86`. |

### Cycle 2 - final verification on `a8faf86`
| Time | Event |
|---|---|
| 02:58-03:10 | `make deploy`: preflight passed; `cdk diff` new stack; `CREATE_COMPLETE`, **166 resources**. |
| 03:10-03:14 | `make seed`; `ALLOW_PAID=1 make verify`: **28/29** (only the latency target failed; VERIFICATION.md). |
| 03:14-03:24 | `make destroy`: stack deleted; 1 CDK provider log group deleted; 7 bootstrap object versions of this cycle deleted. |
| 03:25 | `make verify-cleanup --regions us-east-1 us-west-2`: **No leftovers found** (exit 0). |

### Resource inventory (stack `proto-nl-sandbox`, 166 resources)
VPC with 4 subnets, 4 route tables, internet gateway, 1 NAT gateway + Elastic IP, DynamoDB gateway endpoint, flow
log with its own role; 2 security groups; ElastiCache Serverless cache + user + user group; 4 DynamoDB tables;
2 Secrets Manager secrets; 6 Lambda functions (4 application, 2 CDK providers) and 1 layer; 3 AWS IoT topic rules;
2 API Gateway REST APIs (stages, 10 methods, models, validators, authoriser, API key, usage plan); AWS WAF web ACL +
2 associations; Cognito user pool, domain (prefix from the stack UUID), resource server, 2 clients; AgentCore
Runtime, runtime resource policy, Memory, Gateway, Gateway target, policy engine + Cedar policy, API-key and OAuth2
credential providers; 10 IAM roles / 12 policies; SQS dead-letter queue; 9 log groups; CloudWatch dashboard and
alarm; 4 custom resources (IoT endpoint, runtime log retention, Cognito client secret, default security group
restriction).

### Leftover check (after cycle 2), us-east-1 and us-west-2
| Resource class | Result |
|---|---|
| CloudFormation stack `proto-nl-sandbox` | none |
| AgentCore runtimes, gateways, memories, policy engines, API-key and OAuth2 providers, workload identities (`proto-nl` / `proto_nl`) | none |
| ElastiCache serverless caches, users, user groups | none |
| IoT topic rules, WAF web ACLs, REST APIs, Cognito user pools, dashboards | none |
| Log groups (runtime, Lambda, stack-named) | none (runtime group deleted by its custom resource; provider groups by the cleanup script) |
| DynamoDB tables, SQS queues, Lambda functions and layers, alarms, Secrets Manager secrets incl. scheduled deletion | none |
| Tag index `Project=proto-nl` | no entry that still exists |
| IAM roles and customer managed policies `proto-nl-sandbox*` | none |
| KMS keys, S3 buckets, ECR images | none created by the stack |
| This project's objects in the CDK bootstrap buckets | none (below) |

### CDK bootstrap assets
Identified with `scripts/bootstrap_assets.py`: a template counts only if sha256(content) equals its key and its
Description is this stack's; code objects only if such a template references them from this project's own
resources. CDK's generic provider packages and other applications' objects were not touched (they are still in
the buckets).

| Region | Earlier rounds (2026-10-20) | Round 3, cycle 1 | Round 3, cycle 2 | Deleted | Left |
|---|---|---|---|---|---|
| us-west-2 | 32 | 0 | 0 | 32 | 0 |
| us-east-1 | 8 | 13 | 7 | 28 | 0 |

The 40 earlier object versions (us-west-2 32, us-east-1 8) are the ones listed as leftovers after round 2; they
matched by asset hash (13 + 2 templates whose sha256 equals their key and whose Description is this stack's,
plus every zip those templates reference from this project's functions, layer and agent package).

**Conclusion: 已清理，无残留 / cleaned up, nothing left** - in us-east-1 and us-west-2, after the final cycle
on `a8faf86`.

## Earlier rounds (2026-10-20, before the history rewrite)
Round 1 (container image) and round 2 (direct code deployment) ran in us-west-2 after a first attempt in
us-east-1 failed on the account's VPC quota (rolled back and deleted). Both were destroyed and checked; the only
leftovers were the 40 bootstrap object versions above, which round 3 deleted. During round 1, a temporary
inline policy used to narrow a missing IAM action was removed by CloudFormation together with its role. The
stack name and prefix used then were renamed to `proto-nl` in the rewritten history.
