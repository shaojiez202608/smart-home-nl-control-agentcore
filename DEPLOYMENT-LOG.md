# Deployment log

Account: disposable sandbox account (id not recorded). Credentials from the shell environment. All resources
carried the tags `Project=proto-nl`, `Purpose=prototype`, `Teardown=after-test`. Times are UTC, 2026-10-20. Round 1 (container image) first, round 2 (current code) at the end.

## Attempt 1 - us-east-1 (failed, cleaned up)
| Time | Event |
|---|---|
| 11:01 | `cdk deploy -c config=config/sandbox.json`. Asset build failed: local Docker daemon not running. No CloudFormation changes. Started the local container runtime and retried. |
| ~11:05-11:21 | Stack `proto-nl-sandbox` creation started; `AWS::EC2::VPC` and `AWS::EC2::InternetGateway` failed: *"The maximum number of VPCs has been reached"* / *"internet gateways"*. The Region's VPC quota was fully used by existing resources, which were not touched. |
| 11:24 | Rollback ended in `ROLLBACK_FAILED`: `AWS::BedrockAgentCore::Memory` could not be deleted while still in `CREATING`. |
| ~11:28 | `aws cloudformation delete-stack` retried once the memory was `ACTIVE`; stack deleted. Verified: no stack, no memory, no user pool (a tag-index entry for the user pool was stale; `describe-user-pool` returned not found). |

**Region change:** us-west-2 chosen because it had VPC quota headroom, an existing CDK bootstrap, the
`us.amazon.nova-lite-v1:0` inference profile, and AgentCore Runtime / Memory / Gateway / Identity listed as
available. Region was set with `AWS_REGION` / `CDK_DEFAULT_REGION` for the commands; repository defaults unchanged.

## Attempt 2 - us-west-2 (succeeded)
| Time | Event |
|---|---|
| 11:30-11:40 | Initial deploy, 160 resources, `CREATE_COMPLETE`. Seed script loaded the synthetic homes. |
| 11:41-14:00 | Iterations (10 updates), each followed by tests: Memory expiry parameter fix; IAM action for record expiry; prompt boundary for direct unlock requests; parallel memory reads and segment metrics; prompt-caching layout (later reverted); preloaded preferences and in-session history; cache signer fix; newest-wins preference reads; logging of rejection details; turnOn-with-value normalisation; agent-failure fallback; config-driven nag acknowledgement; one deploy with Amazon Nova Micro for comparison, then back to Amazon Nova Lite. |
| 12:4x | Diagnosis of `AccessDenied` on `BatchCreateMemoryRecords` with an expiry: IAM simulation allowed the three listed actions; a temporary inline policy `tmp-probe` on the orchestrator role (resource: the one memory ARN) was used to narrow the missing action by wildcard patterns to `bedrock-agentcore:UpdateMemoryRecordExpirySpecification`, which was then added to the stack. The manual removal of `tmp-probe` was blocked by the local tool permission settings; the policy was left granting only non-existent action names and was removed by CloudFormation together with the role during `destroy` (role confirmed deleted). |
| 13:58 | Final verification on the final code with Amazon Nova Lite: e2e 21/22 (see VERIFICATION.md). |

### Resource inventory (stack `proto-nl-sandbox`, us-west-2, 160 resources)
VPC with 4 subnets, 4 route tables, 1 NAT gateway + EIP, internet gateway, DynamoDB gateway endpoint, flow log;
2 security groups; ElastiCache Serverless cache + user + user group; 4 DynamoDB tables; 2 Secrets Manager secrets;
6 Lambda functions (4 application, 2 CDK custom-resource providers) and 1 layer; 3 AWS IoT topic rules;
2 API Gateway REST APIs (stages, 9 methods, models, validators, authoriser, API key, usage plan); AWS WAF web ACL
+ 2 associations; Cognito user pool, domain, resource server, 2 clients; AgentCore Runtime, Memory, Gateway,
Gateway target, API-key and OAuth2 credential providers; 10 IAM roles / 11 policies; SQS dead-letter queue;
8 log groups; CloudWatch dashboard and alarm; 3 custom resources.

## Destroy and leftover check
| Time | Event |
|---|---|
| 14:04-14:20 | `npx aws-cdk@latest destroy -c config=config/sandbox.json --force` -> stack deleted (VPC Lambda ENI release took most of the time). |
| 14:2x | Leftover check in **us-west-2 and us-east-1** by stack name, tags and resource type (see below). |

| Resource class | Result |
|---|---|
| CloudFormation stacks `NlControl*` | none (both Regions) |
| AgentCore runtimes, gateways, memories, API-key / OAuth2 credential providers, workload identities (`proto-nl*`) | none |
| ElastiCache serverless caches, users, user groups (`proto-nl*`) | none |
| IoT topic rules, WAF web ACLs, Cognito user pools, API Gateway APIs, Lambda functions/layers, SQS queues, DynamoDB tables, Secrets Manager secrets (incl. planned deletion) | none |
| VPCs / EIPs with the project tag; NAT gateway | none (NAT gateway in `deleted` state) |
| IAM roles `NlControl*` | none |
| Tag index (`Project=proto-nl`) | 4 stale entries (user pool, flow log, VPC endpoint, NAT gateway); each verified as deleted / not found |
| Log groups created outside the stack (AgentCore Runtime log group, 2 CDK provider Lambda log groups) | deleted manually |
| KMS keys | none created (AWS owned / AWS managed keys only) |
| ECR images in the CDK bootstrap repositories (agent image, 5 tags in us-west-2, 1 in us-east-1) | deleted; matched one-to-one against the locally built asset hashes |
| S3 objects in the CDK bootstrap buckets | see below |

### S3 objects in the CDK bootstrap buckets
The bootstrap buckets are shared by every CDK app in the account (another application also deployed to
us-east-1 the same day). Each object version uploaded on the test day was classified by **content**:
templates by their stack description, Lambda zips by their handler source, layer zips by reading the zip
central directory for `python/smarthome_core/`.

| Region | Project templates | Project Lambda zips | Project layer zips | Shared CDK provider zips (kept) | Other applications (kept) |
|---|---|---|---|---|---|
| us-west-2 | 10 | 4 | 8 | 2 | 0 |
| us-east-1 | 2 | 4 | 2 | 3 | 17 |

Deleting the 30 project object versions was **blocked by the local tool permission settings** in this
session, so they were not deleted. Shared CDK provider assets are intentionally kept: they are
content-addressed and may be referenced by other stacks.

Round-1 conclusion: 30 project object versions left in the two bootstrap buckets (superseded by the final
conclusion below).

## Round 2 - us-west-2 (current code)
| Time | Event |
|---|---|
| 15:4x | Deploy of the reviewed code with the container path. The local container CLI switched to a desktop runtime that requires sign-in, and the image tag step failed; file assets and the template were published, no CloudFormation changes. Retried against the local runtime socket. |
| 16:2x-16:36 | Stack creation; AgentCore Runtime, runtime resource policy, gateway policy engine and Cedar policy created. |
| 16:34-17:07 | The ElastiCache Serverless cache went to `create-failed` with no event message; the same definition had created successfully in round 1 and did again in the next attempt (treated as transient). CloudFormation kept waiting on the resource. |
| 17:07-17:20 | Deploy process stopped; `aws cloudformation delete-stack` on this stack; deleted, failed cache gone. |
| 17:0x-17:20 | Agent switched to direct code deployment so that deploying needs no Docker or other container runtime (DECISIONS.md D26); runtime S3 read scoped to the one package object; lint, tests, eval and both synths re-run. Commit `c750355`. |
| 17:21-17:28 | Preflight passed (no Docker check any more); deploy, 164 resources, `CREATE_COMPLETE`. Runtime created from the zip package. |
| 17:32-17:36 | Seed; e2e 23/25, then 24/25 after fixing a test that read Memory once before indexing (commit `f55efdd`). See VERIFICATION.md. |
| 17:37-17:47 | `npx aws-cdk@latest destroy -c config=config/sandbox.json --force`; stack deleted. |
| 17:4x | Leftover check in us-west-2 and us-east-1 with `scripts/verify_cleanup.py`: 6 service-created log groups (2 AgentCore Runtime, 4 CDK provider Lambdas, from both round-2 stacks) deleted by name; 1 stale tag-index entry (subnet) verified not found, and the script now re-checks subnets and VPCs. Re-run: no leftovers. |
| 17:5x | Agent image from the container attempt deleted from the bootstrap ECR repository (1 tag; repository now has no images). |

Round-2 bootstrap S3 objects (us-west-2), classified by content as in round 1: 3 templates, 4 Lambda zips,
2 layer zips, 1 agent package (10 versions). us-east-1: none from this project (new objects there belong to
another application and were not touched). Deletion was not attempted again because it was blocked by the local
tool permission settings in round 1.

## Conclusion
**仍有残留 / Leftovers remain:** 40 object versions of this project's CDK assets in the two CDK bootstrap S3
buckets (us-west-2: 32, us-east-1: 8); they are listed by key and version id for the
maintainer to delete. No other resource of this project remains in either Region: no stack, AgentCore resources,
cache, IAM roles, log groups, tagged resources or ECR images.
