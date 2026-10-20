# Cleanup

## Destroy the stack

```bash
npx aws-cdk@latest destroy -c config=config/sandbox.json --force
```

With `config/sandbox.json` every resource created by the stack is deleted.

## Resources created outside the stack's own templates

| Resource | Why it exists | How to remove |
|---|---|---|
| Log group `/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT` | Created by AgentCore Runtime for the agent's logs | `aws logs delete-log-group --log-group-name <name>` |
| Log groups `/aws/lambda/<prefix>...` created by the CDK custom-resource provider (`AWS679f...`) | CDK provider function logs | Delete by name if present |
| Container image in the CDK bootstrap ECR repository (`cdk-hnb659fds-container-assets-<ACCOUNT_ID>-<REGION>`) | Agent image asset | `aws ecr batch-delete-image` with the asset tag |
| Lambda layer / function zip objects in the CDK bootstrap bucket | CDK assets | Optional; the bootstrap bucket is shared by all CDK apps in the account |
| Service-linked roles (for example `AWSServiceRoleForBedrockAgentCoreRuntimeIdentity`) | Created by AWS services on first use | Shared by the account; leave in place |
| Local `.demo/` and `.build/` directories | Demo passwords and reports | `rm -rf .demo .build` |

With `config/handover.json`, also delete the retained DynamoDB tables, the ElastiCache serverless cache, its
user group and user, and the AgentCore Memory once they are no longer needed.

## Verify nothing is left
```bash
P=proto-nl   # resourcePrefix
aws cloudformation describe-stacks --stack-name proto-nl-sandbox              # expect: does not exist
aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=proto-nl
aws logs describe-log-groups --log-group-name-prefix /aws/bedrock-agentcore/runtimes/${P}
aws bedrock-agentcore-control list-agent-runtimes --query "agentRuntimes[?starts_with(agentRuntimeName,'${P}')]"
aws bedrock-agentcore-control list-gateways --query "items[?starts_with(name,'${P}')]"
aws bedrock-agentcore-control list-memories
aws bedrock-agentcore-control list-api-key-credential-providers
aws bedrock-agentcore-control list-oauth2-credential-providers
aws elasticache describe-serverless-caches --serverless-cache-name ${P}-cache  # expect: not found
```

## CDK assets in the shared bootstrap bucket
The bootstrap bucket and container repository are shared by all CDK apps in the account. Remove only this
project's assets: images whose tag equals an asset hash you built (`docker image ls | grep cdkasset-`), and
object versions whose content is this project's (template description, Lambda handler source, or a layer zip
containing `python/smarthome_core/`). Keep the generic CDK provider assets (`index.js`, `__entrypoint__.js`),
which other stacks may reference. The bucket is versioned, so delete by version id.
