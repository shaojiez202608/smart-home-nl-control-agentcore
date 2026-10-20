# Deployment log

Account: disposable sandbox account (id not recorded). Credentials from the shell environment. All resources
carried the tags `Project=proto-nl`, `Purpose=prototype`, `Teardown=after-test`. Times are UTC, 2026-10-20.

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
