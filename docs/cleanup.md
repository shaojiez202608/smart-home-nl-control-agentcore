# Cleanup

The commands are `make destroy` and `make verify-cleanup` (see the [README](../README.md#clean-up)).

## What `make destroy` does (`scripts/cleanup.sh`)
1. Asks for the stack name (non-interactive: `CONFIRM_STACK=<stack name>`), then runs `cdk destroy`.
2. Deletes the log groups that services create outside the stack: `/aws/bedrock-agentcore/runtimes/<prefix_>...`
   (AgentCore Runtime) and `/aws/lambda/<stackName>...` (CDK provider functions).
3. Deletes workload identities whose names start with the runtime or gateway prefix.
4. Lists this project's objects in the shared CDK bootstrap bucket, identified by asset hash
   (`scripts/bootstrap_assets.py`, rules below), and deletes them only after a second confirmation
   (non-interactive: `CONFIRM_DATA=yes`). Every version of each key is deleted by version id.
5. With `removalPolicy: retain` (handover profile), lists that the tables, cache and memory were kept; delete them
   by name once you have exported what you need.

A failing step prints a warning and the script continues; `make verify-cleanup` decides whether anything is left.
Run it with every Region you used: `uv run python scripts/verify_cleanup.py --regions <REGION> <REGION>`.

## What `make verify-cleanup` checks (`scripts/verify_cleanup.py`, read-only)
Every list call follows pagination. It exits non-zero on any leftover, and also when a check itself fails.

| Class | Matched by |
|---|---|
| CloudFormation stack | stack name |
| AgentCore runtimes, gateways, memories, policy engines, API-key and OAuth2 credential providers, workload identities | resource prefix |
| ElastiCache serverless caches, users, user groups | resource prefix |
| AWS IoT topic rules, AWS WAF web ACLs, REST APIs, Cognito user pools, CloudWatch dashboards | resource prefix |
| Log groups (runtime, Lambda, stack) | name prefixes |
| DynamoDB tables, SQS queues, Lambda functions, CloudWatch alarms | stack-name prefix |
| Secrets Manager secrets, including those scheduled for deletion | `aws:cloudformation:stack-name` tag |
| Lambda layers | layer description |
| Anything tagged `Project=<tag>` | tag index, then re-checked with the owning service (the index lags behind deletions) |
| IAM roles and customer managed policies | stack-name prefix (global) |
| Objects in the CDK bootstrap bucket | asset hash (below) |

## Bootstrap assets: only this project's objects
The bucket `cdk-hnb659fds-assets-111122223333-<REGION>` is shared by every CDK app in the account. An object is
treated as this project's only when:
* it is a template whose key equals the SHA-256 of its content and whose Description is this stack's description;
  or
* its key is referenced by such a template from one of this project's own resources (the four Lambda functions,
  the shared layer, the agent package of the runtime); or
* its key is in the current build's or a saved deploy's asset manifest under the same rule.

Code referenced only by CDK's generic providers (for example the `AwsCustomResource` provider function) is a
shared, content-addressed package and is never listed or deleted. Objects that cannot be identified this way are
never deleted. The CDKToolkit stack is never modified.

## Not removed automatically
| Resource | Why it exists | What to do |
|---|---|---|
| Service-linked roles (for example for AgentCore Runtime identity) | Created by AWS services on first use; shared by the account | Leave in place |
| Retained data stores (handover profile) | `removalPolicy: retain` | Delete by name after export |
| Local `.demo/`, `.build/`, `.deploy/`, `cdk.out/` | Demo passwords, reports, asset manifests, synth output | Delete locally when done; never commit or package them |

Do not assume a completed command removed everything: the conclusion of a cleanup is either "cleaned up, nothing
left" or "leftovers remain: ..." with each resource and the reason.
