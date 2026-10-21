"""Single CDK stack for the prototype.

Layout (one stack keeps deploy and destroy atomic):

* Network       - VPC (private subnets, NAT), flow logs, security groups
* Data          - ElastiCache Serverless (Valkey, IAM auth), DynamoDB tables
* Platform      - simulated existing platform: REST API (API key) + Lambda,
                  virtual devices and state sync on AWS IoT Core rules
* Identity      - Amazon Cognito user pool (app users) and M2M client
* AgentCore     - Runtime (direct code deployment), Memory, Gateway + OpenAPI target,
                  API-key and OAuth2 credential providers
* Ingress       - app-facing REST API (Cognito authorizer) -> orchestrator
* Observability - dashboard and latency alarm
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aws_cdk import (
    Aws,
    CfnOutput,
    Duration,
    Fn,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cloudwatch as cw
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_dynamodb as ddb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_elasticache as elasticache
from aws_cdk import aws_iam as iam
from aws_cdk import aws_iot as iot
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3_assets as s3_assets
from aws_cdk import aws_secretsmanager as sm
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_wafv2 as wafv2
from aws_cdk import custom_resources as cr
from constructs import Construct

from .nag import apply_suppressions
from .openapi import platform_openapi
from .stack_prefixes import base_model_id

ROOT = Path(__file__).resolve().parents[2]


_RETENTION = {
    1: logs.RetentionDays.ONE_DAY,
    3: logs.RetentionDays.THREE_DAYS,
    5: logs.RetentionDays.FIVE_DAYS,
    7: logs.RetentionDays.ONE_WEEK,
    14: logs.RetentionDays.TWO_WEEKS,
    30: logs.RetentionDays.ONE_MONTH,
    90: logs.RetentionDays.THREE_MONTHS,
}

GATEWAY_SCOPE_SERVER = "smarthome-gateway"
GATEWAY_SCOPE = "tools.invoke"


class NlControlStack(Stack):
    def __init__(
        self, scope: Construct, cid: str, *, config: dict[str, Any], layer_dir: Path, agent_dir: Path, **kw
    ) -> None:
        super().__init__(scope, cid, **kw)
        c = config
        self.cfg = c
        p = c["resourcePrefix"]
        p_ = p.replace("-", "_")
        self.removal = RemovalPolicy.DESTROY if c["removalPolicy"] == "destroy" else RemovalPolicy.RETAIN
        self.retention = _RETENTION[int(c["logRetentionDays"])]
        topic = c["topicPrefix"]

        # ------------------------------------------------------------ network
        flow_logs = self._log_group("VpcFlowLogs")
        # The stack is account-agnostic at synth time (infra/app.py passes no account), so the two AZs
        # come from Fn::GetAZs at deploy time and synth needs no AWS credentials or context lookups.
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=int(c["natGateways"]),
            restrict_default_security_group=True,
            subnet_configuration=[
                ec2.SubnetConfiguration(name="public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24),
                ec2.SubnetConfiguration(
                    name="private", subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS, cidr_mask=24
                ),
            ],
        )
        # Own role instead of the CDK default, so the trust policy can carry the confused-deputy
        # conditions the flow-logs service supports (SECURITY-NOTES.md, trust policies).
        flow_log_role = iam.Role(
            self,
            "FlowLogRole",
            assumed_by=iam.ServicePrincipal(
                "vpc-flow-logs.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{Aws.PARTITION}:ec2:{Aws.REGION}:{Aws.ACCOUNT_ID}:vpc-flow-log/*"
                    },
                },
            ),
        )
        flow_logs.grant_write(flow_log_role)
        vpc.add_flow_log(
            "FlowLog", destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_logs, flow_log_role)
        )
        vpc.add_gateway_endpoint("DynamoDbEndpoint", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)
        private = ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)

        lambda_sg = ec2.SecurityGroup(
            self, "LambdaSg", vpc=vpc, allow_all_outbound=False, description="VPC Lambdas: egress only"
        )
        cache_sg = ec2.SecurityGroup(
            self,
            "CacheSg",
            vpc=vpc,
            allow_all_outbound=False,
            description="Cache: ingress from VPC Lambdas only",
        )
        cache_sg.add_ingress_rule(lambda_sg, ec2.Port.tcp(6379), "Valkey from VPC Lambdas")
        lambda_sg.add_egress_rule(cache_sg, ec2.Port.tcp(6379), "Valkey")
        lambda_sg.add_egress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(443),
            "HTTPS to AWS service endpoints and the platform API (via NAT)",
        )

        # --------------------------------------------------------------- data
        cache_user = elasticache.CfnUser(
            self,
            "CacheUser",
            engine="valkey",
            user_id=f"{p}-app",
            user_name=f"{p}-app",
            authentication_mode={"Type": "iam"},
            # Only the key prefixes and commands the application uses.
            access_string="on ~ctx:* ~state:* ~session:* ~confirm:* ~pending:* ~idem:* ~prefs:* "
            "-@all +@connection +get +set +getdel +del +exists +ttl",
        )
        cache_group = elasticache.CfnUserGroup(
            self, "CacheUserGroup", engine="valkey", user_group_id=f"{p}-users", user_ids=[cache_user.user_id]
        )
        cache_group.node.add_dependency(cache_user)
        cache = elasticache.CfnServerlessCache(
            self,
            "Cache",
            serverless_cache_name=f"{p}-cache",
            engine="valkey",
            description="Preloaded device context, device state, sessions, confirmation tokens",
            security_group_ids=[cache_sg.security_group_id],
            subnet_ids=vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS).subnet_ids,
            user_group_id=cache_group.user_group_id,
        )
        cache.node.add_dependency(cache_group)
        cache.apply_removal_policy(self.removal)

        def table(name: str, pk: str, ttl: str | None = None) -> ddb.Table:
            return ddb.Table(
                self,
                name,
                partition_key=ddb.Attribute(name=pk, type=ddb.AttributeType.STRING),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                encryption=ddb.TableEncryption.AWS_MANAGED,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                time_to_live_attribute=ttl,
                removal_policy=self.removal,
            )

        devices_t = table("DevicesTable", "uuid")
        devices_t.add_global_secondary_index(
            index_name="byHome", partition_key=ddb.Attribute(name="homeId", type=ddb.AttributeType.STRING)
        )
        members_t = table("MembersTable", "userId")
        members_t.add_global_secondary_index(
            index_name="byHome", partition_key=ddb.Attribute(name="homeId", type=ddb.AttributeType.STRING)
        )
        commands_t = table("CommandsTable", "commandId", ttl="expiresAt")
        speakers_t = table("SpeakersTable", "speakerId")

        api_key_secret = sm.Secret(
            self,
            "PlatformApiKeySecret",
            description="API key of the (simulated) existing platform API",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template="{}",
                generate_string_key="apiKey",
                exclude_punctuation=True,
                password_length=40,
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ------------------------------------------------------------- shared
        # Stack-owned log group (with retention) for CDK's AwsCustomResource provider function, instead of
        # the never-expiring group Lambda would create outside the stack.
        self.provider_logs = self._log_group("CustomResourceProviderLogs")
        self.layer = lambda_.LayerVersion(
            self,
            "SharedLayer",
            code=lambda_.Code.from_asset(str(layer_dir)),
            compatible_runtimes=[lambda_.Runtime.PYTHON_3_14],
            compatible_architectures=[lambda_.Architecture.ARM_64],
            description="smarthome_core + smarthome_aws + pinned deps",
            removal_policy=RemovalPolicy.DESTROY,
        )
        iot_ep = cr.AwsCustomResource(
            self,
            "IotDataEndpoint",
            on_update=cr.AwsSdkCall(
                service="Iot",
                action="describeEndpoint",
                parameters={"endpointType": "iot:Data-ATS"},
                physical_resource_id=cr.PhysicalResourceId.of("iot-data-ats-endpoint"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_sdk_calls(
                resources=cr.AwsCustomResourcePolicy.ANY_RESOURCE
            ),
            install_latest_aws_sdk=False,
            log_group=self.provider_logs,
        )
        iot_endpoint = "https://" + iot_ep.get_response_field("endpointAddress")
        topic_arn = f"arn:{Aws.PARTITION}:iot:{Aws.REGION}:{Aws.ACCOUNT_ID}:topic/{topic}"

        def publish(fn: lambda_.Function, pattern: str) -> None:
            fn.add_to_role_policy(
                iam.PolicyStatement(actions=["iot:Publish"], resources=[f"{topic_arn}/{pattern}"])
            )

        # ----------------------------------------------- simulated platform
        platform_fn = self._function(
            "PlatformApiFn",
            "platform_api",
            env={
                "DEVICES_TABLE": devices_t.table_name,
                "MEMBERS_TABLE": members_t.table_name,
                "COMMANDS_TABLE": commands_t.table_name,
                "SPEAKERS_TABLE": speakers_t.table_name,
                "TOPIC_PREFIX": topic,
                "IOT_DATA_ENDPOINT": iot_endpoint,
            },
        )
        devices_t.grant_read_data(platform_fn)
        members_t.grant_read_data(platform_fn)
        speakers_t.grant_read_data(platform_fn)
        commands_t.grant_read_write_data(platform_fn)
        publish(platform_fn, "devices/*/cmd")

        platform_api = self._rest_api("PlatformApi", f"{p}-platform-api", "v1", api_key_required=True)
        integ = apigw.LambdaIntegration(platform_fn)
        pv = platform_api.add_request_validator(
            "PlatformValidator", validate_request_parameters=True, validate_request_body=True
        )

        def path_params(*names: str) -> dict[str, bool]:
            return {f"method.request.path.{n}": True for n in names}

        users = platform_api.root.add_resource("members").add_resource("{userId}")
        users.add_resource("devices").add_method(
            "GET", integ, request_validator=pv, request_parameters=path_params("userId")
        )
        dev = platform_api.root.add_resource("devices").add_resource("{uuid}")
        dev.add_resource("status").add_method(
            "GET",
            integ,
            request_validator=pv,
            request_parameters={**path_params("uuid"), "method.request.querystring.userId": True},
        )
        dev.add_resource("commands").add_method(
            "POST", integ, request_validator=pv, request_parameters=path_params("uuid")
        )
        platform_api.root.add_resource("commands").add_resource("{commandId}").add_method(
            "GET",
            integ,
            request_validator=pv,
            request_parameters={**path_params("commandId"), "method.request.querystring.userId": True},
        )
        platform_api.root.add_resource("speakers").add_resource("{speakerId}").add_method(
            "GET", integ, request_validator=pv, request_parameters=path_params("speakerId")
        )
        key = platform_api.add_api_key(
            "PlatformApiKey", value=api_key_secret.secret_value_from_json("apiKey").unsafe_unwrap()
        )
        plan = platform_api.add_usage_plan(
            "PlatformUsagePlan",
            throttle=apigw.ThrottleSettings(
                rate_limit=c["apiThrottle"]["rateLimit"] * 5, burst_limit=c["apiThrottle"]["burstLimit"] * 5
            ),
            api_stages=[apigw.UsagePlanPerApiStage(api=platform_api, stage=platform_api.deployment_stage)],
        )
        plan.add_api_key(key)

        dlq = sqs.Queue(
            self,
            "AsyncDlq",
            enforce_ssl=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(4),
            removal_policy=RemovalPolicy.DESTROY,
        )
        device_fn = self._function(
            "VirtualDeviceFn",
            "virtual_device",
            env={"TOPIC_PREFIX": topic, "IOT_DATA_ENDPOINT": iot_endpoint},
            timeout=10,
            dlq=dlq,
        )
        publish(device_fn, "devices/*/state")

        state_fn = self._function(
            "StateSyncFn",
            "state_sync",
            env={
                "DEVICES_TABLE": devices_t.table_name,
                "MEMBERS_TABLE": members_t.table_name,
                "COMMANDS_TABLE": commands_t.table_name,
                "TOPIC_PREFIX": topic,
                "IOT_DATA_ENDPOINT": iot_endpoint,
                "METRICS_NAMESPACE": "SmartHomeNL",
                **self._cache_env(cache, cache_user),
            },
            vpc=vpc,
            subnets=private,
            sg=lambda_sg,
            dlq=dlq,
        )
        devices_t.grant_read_write_data(state_fn)
        members_t.grant_read_data(state_fn)
        commands_t.grant_read_write_data(state_fn)
        publish(state_fn, "members/*/events")
        self._grant_cache(state_fn, cache, cache_user)

        rule_errors = self._log_group("IotRuleErrors")
        rule_role = iam.Role(
            self,
            "IotRuleErrorRole",
            assumed_by=iam.ServicePrincipal(
                "iot.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    # Only this stack's topic rules (all named <prefix_>_...).
                    "ArnLike": {
                        "aws:SourceArn": f"arn:{Aws.PARTITION}:iot:{Aws.REGION}:{Aws.ACCOUNT_ID}:rule/{p_}_*"
                    },
                },
            ),
        )
        rule_errors.grant_write(rule_role)
        self._iot_rule(
            f"{p_}_device_cmd",
            f"SELECT *, topic(3) AS uuid FROM '{topic}/devices/+/cmd'",
            device_fn,
            rule_errors,
            rule_role,
        )
        self._iot_rule(
            f"{p_}_device_state",
            f"SELECT *, topic(3) AS uuid FROM '{topic}/devices/+/state'",
            state_fn,
            rule_errors,
            rule_role,
        )

        # ------------------------------------------------------------ identity
        pool = cognito.UserPool(
            self,
            "UserPool",
            user_pool_name=f"{p}-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True),
            password_policy=cognito.PasswordPolicy(
                min_length=14,
                require_digits=True,
                require_lowercase=True,
                require_uppercase=True,
                require_symbols=True,
            ),
            account_recovery=cognito.AccountRecovery.NONE,
            removal_policy=RemovalPolicy.DESTROY,
        )
        # Globally unique, but without the account id: the first block of the stack's UUID.
        stack_uuid_head = Fn.select(0, Fn.split("-", Fn.select(2, Fn.split("/", Aws.STACK_ID))))
        domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=Fn.join("-", [p, stack_uuid_head])),
        )
        rs = pool.add_resource_server(
            "GatewayResourceServer",
            identifier=GATEWAY_SCOPE_SERVER,
            scopes=[
                cognito.ResourceServerScope(
                    scope_name=GATEWAY_SCOPE, scope_description="Invoke gateway tools"
                )
            ],
        )
        m2m = pool.add_client(
            "GatewayM2MClient",
            generate_secret=True,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(
                        rs,
                        cognito.ResourceServerScope(
                            scope_name=GATEWAY_SCOPE, scope_description="Invoke gateway tools"
                        ),
                    )
                ],
            ),
            access_token_validity=Duration.minutes(60),
            prevent_user_existence_errors=True,
        )
        app_client = pool.add_client(
            "AppClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_srp=True, user_password=True),
            id_token_validity=Duration.minutes(60),
            access_token_validity=Duration.minutes(60),
            prevent_user_existence_errors=True,
        )
        m2m_secret = sm.Secret(
            self,
            "GatewayClientSecret",
            description="Cognito M2M client secret used by AgentCore Identity to reach the gateway",
            secret_object_value={"clientSecret": m2m.user_pool_client_secret},
            removal_policy=RemovalPolicy.DESTROY,
        )
        discovery = f"https://cognito-idp.{Aws.REGION}.amazonaws.com/{pool.user_pool_id}/.well-known/openid-configuration"

        # ----------------------------------------------------------- agentcore
        memory = agentcore.CfnMemory(
            self,
            "Memory",
            name=f"{p_}_memory",
            description="Short-term turns and governed long-term preferences",
            event_expiry_duration=int(c["memoryEventExpiryDays"]),
            indexed_keys=[
                agentcore.CfnMemory.IndexedKeyProperty(key="deviceUuid", type="STRING"),
                agentcore.CfnMemory.IndexedKeyProperty(key="attribute", type="STRING"),
            ],
        )
        memory.apply_removal_policy(self.removal)

        api_key_provider = agentcore.CfnApiKeyCredentialProvider(
            self,
            "PlatformApiKeyProvider",
            name=f"{p}-platform-api-key",
            api_key_secret_source="EXTERNAL",  # pragma: allowlist secret - enum value, not a secret
            api_key_secret_config=agentcore.CfnApiKeyCredentialProvider.SecretReferenceProperty(
                secret_id=api_key_secret.secret_arn, json_key="apiKey"
            ),
        )
        oauth_provider_name = f"{p}-gateway-m2m"
        oauth_provider = agentcore.CfnOAuth2CredentialProvider(
            self,
            "GatewayOAuthProvider",
            name=oauth_provider_name,
            credential_provider_vendor="CustomOauth2",
            oauth2_provider_config_input={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {"discoveryUrl": discovery},
                    "clientId": m2m.user_pool_client_id,
                    "clientSecretSource": "EXTERNAL",  # pragma: allowlist secret - enum value
                    "clientSecretConfig": {"secretId": m2m_secret.secret_arn, "jsonKey": "clientSecret"},
                }
            },
        )

        ac_arn = f"arn:{Aws.PARTITION}:bedrock-agentcore:{Aws.REGION}:{Aws.ACCOUNT_ID}"
        directory = f"{ac_arn}:workload-identity-directory/default"
        # AgentCore names the workload identity after the runtime / gateway (service-generated suffix).
        runtime_identity = [
            directory,
            f"{directory}/workload-identity/{p_}_agent*",
            f"{ac_arn}:token-vault/default",
        ]
        gateway_identity = [
            directory,
            f"{directory}/workload-identity/{p}-gateway*",
            f"{ac_arn}:token-vault/default",
        ]

        gateway_arn_pattern = f"{ac_arn}:gateway/{p}-gateway*"
        gateway_role = iam.Role(
            self,
            "GatewayRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    "ArnLike": {"aws:SourceArn": gateway_arn_pattern},
                },
            ),
        )
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock-agentcore:GetWorkloadAccessToken", "bedrock-agentcore:GetResourceApiKey"],
                resources=[*gateway_identity, api_key_provider.attr_credential_provider_arn],
            )
        )
        api_key_secret.grant_read(gateway_role)

        # Default-deny Cedar policy engine: the agent may only call read-only tools,
        # even though the gateway exposes the platform's controlDevice operation.
        policy_engine = agentcore.CfnPolicyEngine(
            self,
            "GatewayPolicyEngine",
            name=f"{p_}_tool_policies",
            description="Default deny; read-only platform tools allowed for the agent's M2M client",
        )
        gateway_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:GetPolicyEngine",
                    "bedrock-agentcore:AuthorizeAction",
                    "bedrock-agentcore:PartiallyAuthorizeActions",
                ],
                resources=[policy_engine.attr_policy_engine_arn, gateway_arn_pattern],
            )
        )

        gateway = agentcore.CfnGateway(
            self,
            "Gateway",
            name=f"{p}-gateway",
            description="Wraps the existing platform REST API as MCP tools",
            role_arn=gateway_role.role_arn,
            protocol_type="MCP",
            authorizer_type="CUSTOM_JWT",
            authorizer_configuration={
                "customJwtAuthorizer": {
                    "discoveryUrl": discovery,
                    "allowedClients": [m2m.user_pool_client_id],
                }
            },
            policy_engine_configuration={
                "arn": policy_engine.attr_policy_engine_arn,
                "mode": c.get("gatewayPolicyMode", "ENFORCE"),
            },
        )
        gateway.node.add_dependency(gateway_role)
        target = agentcore.CfnGatewayTarget(
            self,
            "PlatformTarget",
            gateway_identifier=gateway.attr_gateway_identifier,
            name="platform",
            description="Existing platform API: list devices, device status, control device",
            target_configuration={
                "mcp": {
                    "openApiSchema": {
                        "inlinePayload": self.to_json_string(platform_openapi(platform_api.url))
                    }
                }
            },
            credential_provider_configurations=[
                {
                    "credentialProviderType": "API_KEY",
                    "credentialProvider": {
                        "apiKeyCredentialProvider": {
                            "providerArn": api_key_provider.attr_credential_provider_arn,
                            "credentialParameterName": "x-api-key",
                            "credentialLocation": "HEADER",
                        }
                    },
                }
            ],
        )
        target.node.add_dependency(gateway)

        issuer = f"https://cognito-idp.{Aws.REGION}.amazonaws.com/{pool.user_pool_id}"
        read_only_tools = ", ".join(
            f'AgentCore::Action::"platform___{op}"' for op in ("queryDeviceStatus", "getUserDevices")
        )
        tool_policy = agentcore.CfnPolicy(
            self,
            "ReadOnlyToolsPolicy",
            policy_engine_id=policy_engine.attr_policy_engine_id,
            name="allow_read_only_platform_tools",
            description="Permit the read-only platform tools for tokens issued by this stack's user pool",
            definition={
                "cedar": {
                    "statement": (
                        f"permit(principal is AgentCore::OAuthUser, action in [{read_only_tools}], "
                        f'resource == AgentCore::Gateway::"{gateway.attr_gateway_arn}") '
                        f'when {{ principal.hasTag("iss") && principal.getTag("iss") == "{issuer}" }};'
                    )
                }
            },
            validation_mode="FAIL_ON_ANY_FINDINGS",
        )
        # Policy validation reads the gateway's tool schema, which exists once the target has synced.
        tool_policy.node.add_dependency(target)

        # Direct code deployment: ARM64 wheels and agent source, zipped by CDK (see build.py).
        agent_code = s3_assets.Asset(self, "AgentCode", path=str(agent_dir))
        runtime_role = iam.Role(
            self,
            "RuntimeRole",
            assumed_by=iam.ServicePrincipal(
                "bedrock-agentcore.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": Aws.ACCOUNT_ID},
                    # Only this stack's runtime (AgentCore appends a suffix to the runtime name).
                    "ArnLike": {"aws:SourceArn": f"{ac_arn}:runtime/{p_}_agent*"},
                },
            ),
        )
        model_id = c["modelId"]
        base_model = base_model_id(model_id)
        runtime_policy = iam.Policy(
            self,
            "RuntimePolicy",
            statements=[
                iam.PolicyStatement(
                    actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                    resources=[
                        f"arn:{Aws.PARTITION}:bedrock:{Aws.REGION}:{Aws.ACCOUNT_ID}:inference-profile/{model_id}",
                        f"arn:{Aws.PARTITION}:bedrock:*::foundation-model/{base_model}",
                    ],
                ),
                # Only the one deployment package object, not the whole bootstrap bucket.
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[agent_code.bucket.arn_for_objects(agent_code.s3_object_key)],
                ),
                iam.PolicyStatement(
                    actions=[
                        "bedrock-agentcore:ListMemoryRecords",
                        "bedrock-agentcore:ListEvents",
                        "bedrock-agentcore:CreateEvent",
                    ],
                    resources=[memory.attr_memory_arn],
                ),
                # The runtime delivers the workload access token to the container (the
                # service-linked role obtains it), so only the token-vault read is granted,
                # scoped to the one OAuth2 provider.
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:GetResourceOauth2Token"],
                    resources=[*runtime_identity, oauth_provider.attr_credential_provider_arn],
                ),
                iam.PolicyStatement(
                    actions=[
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                    ],
                    resources=[
                        f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*"
                    ],
                ),
                iam.PolicyStatement(actions=["logs:DescribeLogGroups"], resources=["*"]),
                iam.PolicyStatement(
                    actions=[
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                ),
            ],
        )
        runtime_role.attach_inline_policy(runtime_policy)
        # No GetSecretValue on the M2M secret for the runtime: AgentCore Identity reads the client secret
        # for the token vault; verified by the end-to-end status-query check (DEPLOYMENT-LOG.md).

        runtime = agentcore.CfnRuntime(
            self,
            "Runtime",
            agent_runtime_name=f"{p_}_agent",
            description="Strands agent that turns utterances into structured plans",
            role_arn=runtime_role.role_arn,
            agent_runtime_artifact={
                "codeConfiguration": {
                    "code": {"s3": {"bucket": agent_code.s3_bucket_name, "prefix": agent_code.s3_object_key}},
                    "runtime": "PYTHON_3_13",
                    "entryPoint": ["opentelemetry-instrument", "main.py"],
                }
            },
            network_configuration={"networkMode": "PUBLIC"},
            protocol_configuration="HTTP",
            # Aligned with the orchestrator's session idle TTL, so a session id is not reused
            # after its microVM (and the in-process history) is gone.
            lifecycle_configuration={
                "idleRuntimeSessionTimeout": int(c["sessionIdleSeconds"]),
                "maxLifetime": int(c["runtimeMaxLifetimeSeconds"]),
            },
            environment_variables={
                "AGENT_OBSERVABILITY_ENABLED": "true",
                "OTEL_PYTHON_DISTRO": "aws_distro",
                "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
                # Keep prompts and model output out of the runtime's telemetry logs.
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "false",
                "MODEL_ID": model_id,
                "MODEL_MAX_TOKENS": str(c["modelMaxTokens"]),
                "MEMORY_ID": memory.attr_memory_id,
                "GATEWAY_URL": gateway.attr_gateway_url,
                "GATEWAY_TARGET_NAME": "platform",
                "OAUTH_PROVIDER_NAME": oauth_provider_name,
                "OAUTH_SCOPE": f"{GATEWAY_SCOPE_SERVER}/{GATEWAY_SCOPE}",
            },
        )
        runtime.node.add_dependency(runtime_policy)
        runtime.node.add_dependency(runtime_role)
        runtime.node.add_dependency(oauth_provider)
        runtime.node.add_dependency(target)

        # AgentCore creates /aws/bedrock-agentcore/runtimes/<runtime id>-DEFAULT itself (never expires); a
        # LogGroup resource with that name would collide. A custom resource sets the configured retention once
        # the runtime exists and deletes the group on stack deletion.
        runtime_log_group = f"/aws/bedrock-agentcore/runtimes/{runtime.attr_agent_runtime_id}-DEFAULT"
        runtime_log_group_arn = (
            f"arn:{Aws.PARTITION}:logs:{Aws.REGION}:{Aws.ACCOUNT_ID}:log-group:{runtime_log_group}"
        )
        retention_call = cr.AwsSdkCall(
            service="CloudWatchLogs",
            action="putRetentionPolicy",
            parameters={"logGroupName": runtime_log_group, "retentionInDays": int(c["logRetentionDays"])},
            physical_resource_id=cr.PhysicalResourceId.of(runtime_log_group),
        )
        runtime_logs = cr.AwsCustomResource(
            self,
            "RuntimeLogRetention",
            on_create=retention_call,
            on_update=retention_call,
            on_delete=cr.AwsSdkCall(
                service="CloudWatchLogs",
                action="deleteLogGroup",
                parameters={"logGroupName": runtime_log_group},
                ignore_error_codes_matching="ResourceNotFoundException",
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements(
                [
                    iam.PolicyStatement(
                        actions=["logs:PutRetentionPolicy", "logs:DeleteLogGroup"],
                        resources=[runtime_log_group_arn, f"{runtime_log_group_arn}:*"],
                    )
                ]
            ),
            install_latest_aws_sdk=False,
            log_group=self.provider_logs,
        )
        runtime_logs.node.add_dependency(runtime)

        # ------------------------------------------------------- orchestrator
        orch_fn = self._function(
            "OrchestratorFn",
            "orchestrator",
            env={
                "PLATFORM_API_URL": platform_api.url,
                "PLATFORM_API_KEY_SECRET_ARN": api_key_secret.secret_arn,
                "AGENT_RUNTIME_ARN": runtime.attr_agent_runtime_arn,
                "MEMORY_ID": memory.attr_memory_id,
                "PREFERENCE_TTL_DAYS": str(c["preferenceTtlDays"]),
                "TOPIC_PREFIX": topic,
                "IOT_DATA_ENDPOINT": iot_endpoint,
                "METRICS_NAMESPACE": "SmartHomeNL",
                "SESSION_IDLE_SECONDS": str(c["sessionIdleSeconds"]),
                **self._cache_env(cache, cache_user),
            },
            vpc=vpc,
            subnets=private,
            sg=lambda_sg,
            timeout=25,
            memory=1024,
            dlq=dlq,
        )
        api_key_secret.grant_read(orch_fn)
        self._grant_cache(orch_fn, cache, cache_user)
        publish(orch_fn, "speakers/*/reply")
        # InvokeAgentRuntimeForUser: the orchestrator passes the verified user id as runtimeUserId, which the
        # runtime needs to hand the agent a workload access token (D25). Only this role may do either.
        invoke_actions = [
            "bedrock-agentcore:InvokeAgentRuntime",
            "bedrock-agentcore:InvokeAgentRuntimeForUser",
        ]
        orch_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=invoke_actions,
                resources=[
                    runtime.attr_agent_runtime_arn,
                    f"{runtime.attr_agent_runtime_arn}/runtime-endpoint/*",
                ],
            )
        )
        # Resource policy: only the orchestrator may invoke the runtime (and assert a user id),
        # whatever identity-based policies other principals in the account might have.
        agentcore.CfnResourcePolicy(
            self,
            "RuntimeResourcePolicy",
            resource_arn=runtime.attr_agent_runtime_arn,
            policy=self.to_json_string(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AllowOrchestratorOnly",
                            "Effect": "Allow",
                            "Principal": {"AWS": orch_fn.role.role_arn},
                            "Action": invoke_actions,
                            "Resource": runtime.attr_agent_runtime_arn,
                        },
                        {
                            "Sid": "DenyEveryoneElse",
                            "Effect": "Deny",
                            "Principal": {"AWS": "*"},
                            "Action": invoke_actions,
                            "Resource": runtime.attr_agent_runtime_arn,
                            "Condition": {"ArnNotEquals": {"aws:PrincipalArn": orch_fn.role.role_arn}},
                        },
                    ],
                }
            ),
        )
        orch_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock-agentcore:ListMemoryRecords",
                    "bedrock-agentcore:BatchCreateMemoryRecords",
                    "bedrock-agentcore:BatchDeleteMemoryRecords",
                    # Authorised separately when a record carries an expiry specification
                    # (found by deployment testing; see DEPLOYMENT-LOG.md).
                    "bedrock-agentcore:UpdateMemoryRecordExpirySpecification",
                ],
                resources=[memory.attr_memory_arn],
            )
        )
        self._iot_rule(
            f"{p_}_speaker_utterance",
            f"SELECT utterance, topic(3) AS speakerId, 'speaker' AS source FROM '{topic}/speakers/+/utterance'",
            orch_fn,
            rule_errors,
            rule_role,
        )

        # ------------------------------------------------------------- ingress
        app_api = self._rest_api("AppApi", f"{p}-app-api", "api", api_key_required=False)
        authorizer = apigw.CognitoUserPoolsAuthorizer(self, "AppAuthorizer", cognito_user_pools=[pool])
        auth = {"authorizer": authorizer, "authorization_type": apigw.AuthorizationType.COGNITO}
        oi = apigw.LambdaIntegration(orch_fn)
        validator = app_api.add_request_validator(
            "AppValidator", validate_request_body=True, validate_request_parameters=True
        )
        cmd_model = app_api.add_model(
            "CommandModel",
            content_type="application/json",
            schema=apigw.JsonSchema(
                type=apigw.JsonSchemaType.OBJECT,
                required=["utterance"],
                properties={
                    "utterance": apigw.JsonSchema(
                        type=apigw.JsonSchemaType.STRING, min_length=1, max_length=500
                    ),
                    "idempotencyKey": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, max_length=128),
                },
            ),
        )
        confirm_model = app_api.add_model(
            "ConfirmModel",
            content_type="application/json",
            schema=apigw.JsonSchema(
                type=apigw.JsonSchemaType.OBJECT,
                required=["token"],
                properties={
                    "token": apigw.JsonSchema(type=apigw.JsonSchemaType.STRING, min_length=8, max_length=64)
                },
            ),
        )
        v1 = app_api.root.add_resource("v1")
        cmds = v1.add_resource("commands")
        cmds.add_method(
            "POST", oi, request_validator=validator, request_models={"application/json": cmd_model}, **auth
        )
        cmds.add_resource("{commandId}").add_method(
            "GET",
            oi,
            request_validator=validator,
            request_parameters={"method.request.path.commandId": True},
            **auth,
        )
        v1.add_resource("devices").add_method("GET", oi, request_validator=validator, **auth)
        confs = v1.add_resource("confirmations")
        confs.add_method("GET", oi, request_validator=validator, **auth)
        confs.add_method(
            "POST",
            oi,
            request_validator=validator,
            request_models={"application/json": confirm_model},
            **auth,
        )

        if c.get("enableWaf", True):
            self._waf([platform_api, app_api], p)

        # -------------------------------------------------------- observability
        self._dashboard(p, orch_fn, platform_fn, state_fn, app_api, int(c["acceptanceLatencyTargetMs"]))

        out = {
            "Region": Aws.REGION,
            "AppApiUrl": app_api.url,
            "PlatformApiUrl": platform_api.url,
            "UserPoolId": pool.user_pool_id,
            "AppClientId": app_client.user_pool_client_id,
            "DevicesTable": devices_t.table_name,
            "MembersTable": members_t.table_name,
            "SpeakersTable": speakers_t.table_name,
            "CommandsTable": commands_t.table_name,
            "AgentRuntimeArn": runtime.attr_agent_runtime_arn,
            "GatewayUrl": gateway.attr_gateway_url,
            "MemoryId": memory.attr_memory_id,
            "TopicPrefix": topic,
            "IotDataEndpoint": iot_endpoint,
            "PlatformApiKeySecretArn": api_key_secret.secret_arn,
            # Used by the e2e test to verify the gateway's Cedar policy with the agent's own client.
            "GatewayClientId": m2m.user_pool_client_id,
            "GatewayClientSecretArn": m2m_secret.secret_arn,
            "TokenEndpoint": f"{domain.base_url()}/oauth2/token",
            "GatewayScope": f"{GATEWAY_SCOPE_SERVER}/{GATEWAY_SCOPE}",
        }
        for k, v in out.items():
            CfnOutput(self, f"Out{k}", key=k, value=v)

        apply_suppressions(self, c, base_model)

    # ---------------------------------------------------------------- helpers

    def _log_group(self, cid: str) -> logs.LogGroup:
        return logs.LogGroup(self, cid, retention=self.retention, removal_policy=RemovalPolicy.DESTROY)

    def _function(
        self,
        cid: str,
        folder: str,
        *,
        env: dict[str, str],
        vpc: ec2.IVpc | None = None,
        subnets: ec2.SubnetSelection | None = None,
        sg: ec2.ISecurityGroup | None = None,
        timeout: int = 10,
        memory: int = 512,
        dlq: sqs.IQueue | None = None,
    ) -> lambda_.Function:
        return lambda_.Function(
            self,
            cid,
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(str(ROOT / "src" / "functions" / folder)),
            layers=[self.layer],
            environment=env,
            timeout=Duration.seconds(timeout),
            memory_size=memory,
            tracing=lambda_.Tracing.ACTIVE,
            log_group=self._log_group(f"{cid}Logs"),
            vpc=vpc,
            vpc_subnets=subnets,
            security_groups=[sg] if sg else None,
            dead_letter_queue=dlq,
        )

    @staticmethod
    def _cache_env(cache: elasticache.CfnServerlessCache, user: elasticache.CfnUser) -> dict[str, str]:
        return {
            "CACHE_ENDPOINT": cache.attr_endpoint_address,
            "CACHE_PORT": cache.attr_endpoint_port,
            "CACHE_NAME": cache.serverless_cache_name,
            "CACHE_USER_ID": user.user_id,
        }

    @staticmethod
    def _grant_cache(
        fn: lambda_.Function, cache: elasticache.CfnServerlessCache, user: elasticache.CfnUser
    ) -> None:
        fn.add_to_role_policy(
            iam.PolicyStatement(actions=["elasticache:Connect"], resources=[cache.attr_arn, user.attr_arn])
        )

    def _rest_api(self, cid: str, name: str, stage: str, *, api_key_required: bool) -> apigw.RestApi:
        access = self._log_group(f"{cid}AccessLogs")
        t = self.cfg["apiThrottle"]
        return apigw.RestApi(
            self,
            cid,
            rest_api_name=name,
            endpoint_types=[apigw.EndpointType.REGIONAL],
            cloud_watch_role=False,  # uses the account's existing API Gateway logging role
            default_method_options=apigw.MethodOptions(api_key_required=api_key_required),
            deploy_options=apigw.StageOptions(
                stage_name=stage,
                tracing_enabled=True,
                metrics_enabled=True,
                logging_level=apigw.MethodLoggingLevel.ERROR,
                data_trace_enabled=False,
                throttling_rate_limit=t["rateLimit"] * (5 if api_key_required else 1),
                throttling_burst_limit=t["burstLimit"] * (5 if api_key_required else 1),
                access_log_destination=apigw.LogGroupLogDestination(access),
                access_log_format=apigw.AccessLogFormat.json_with_standard_fields(
                    caller=False,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
            ),
        )

    def _iot_rule(
        self, name: str, sql: str, fn: lambda_.Function, errors: logs.LogGroup, role: iam.Role
    ) -> iot.CfnTopicRule:
        rule = iot.CfnTopicRule(
            self,
            f"Rule{name}",
            rule_name=name,
            topic_rule_payload=iot.CfnTopicRule.TopicRulePayloadProperty(
                sql=sql,
                aws_iot_sql_version="2016-03-23",
                rule_disabled=False,
                actions=[
                    iot.CfnTopicRule.ActionProperty(
                        lambda_=iot.CfnTopicRule.LambdaActionProperty(function_arn=fn.function_arn)
                    )
                ],
                error_action=iot.CfnTopicRule.ActionProperty(
                    cloudwatch_logs=iot.CfnTopicRule.CloudwatchLogsActionProperty(
                        log_group_name=errors.log_group_name, role_arn=role.role_arn
                    )
                ),
            ),
        )
        fn.add_permission(
            f"Invoke{name}",
            principal=iam.ServicePrincipal("iot.amazonaws.com"),
            source_arn=rule.attr_arn,
            source_account=Aws.ACCOUNT_ID,
        )
        return rule

    def _waf(self, apis: list[apigw.RestApi], prefix: str) -> None:
        def managed(name: str, prio: int) -> wafv2.CfnWebACL.RuleProperty:
            return wafv2.CfnWebACL.RuleProperty(
                name=name,
                priority=prio,
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS", name=name
                    )
                ),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True, metric_name=name, sampled_requests_enabled=True
                ),
            )

        acl = wafv2.CfnWebACL(
            self,
            "WebAcl",
            name=f"{prefix}-web-acl",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{prefix}-web-acl",
                sampled_requests_enabled=True,
            ),
            rules=[
                managed("AWSManagedRulesCommonRuleSet", 1),
                managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimitPerIp",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=int(self.cfg["apiThrottle"]["rateLimit"]) * 60, aggregate_key_type="IP"
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimitPerIp",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )
        for i, api in enumerate(apis):
            stage = api.deployment_stage
            assoc = wafv2.CfnWebACLAssociation(
                self,
                f"WebAclAssoc{i}",
                web_acl_arn=acl.attr_arn,
                resource_arn=f"arn:{Aws.PARTITION}:apigateway:{Aws.REGION}::/restapis/{api.rest_api_id}/stages/{stage.stage_name}",
            )
            assoc.node.add_dependency(stage)

    def _dashboard(
        self,
        prefix: str,
        orch: lambda_.Function,
        platform: lambda_.Function,
        state: lambda_.Function,
        app_api: apigw.RestApi,
        target_ms: int,
    ) -> None:
        ns = "SmartHomeNL"
        dims = {"Service": "orchestrator"}

        def m(name: str, stat: str) -> cw.Metric:
            return cw.Metric(
                namespace=ns,
                metric_name=name,
                dimensions_map=dims,
                statistic=stat,
                period=Duration.minutes(1),
            )

        total_p95 = m("LatencyTotal", "p95")
        cw.Alarm(
            self,
            "AcceptanceLatencyAlarm",
            alarm_description="Acceptance latency P95 above the configured target",
            metric=total_p95,
            threshold=target_ms,
            evaluation_periods=5,
            datapoints_to_alarm=3,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        )
        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"{prefix}-dashboard")
        dash.add_widgets(
            cw.GraphWidget(
                title="Acceptance latency (independently measured, ms)",
                left=[m("LatencyTotal", s) for s in ("p50", "p95", "p99")],
                left_annotations=[cw.HorizontalAnnotation(value=target_ms, label="target")],
                width=12,
            ),
            cw.GraphWidget(
                title="Segment latency p95 (do not add percentiles)",
                left=[m(f"Latency{s}", "p95") for s in ("Preload", "Inference", "Dispatch")]
                + [m("AgentMemoryReadMs", "p95"), m("AgentModelMs", "p95")],
                width=12,
            ),
            cw.GraphWidget(
                title="Hard checks",
                left=[
                    m(n, "Sum")
                    for n in (
                        "ActionsRejected",
                        "SensitiveActionsHeld",
                        "AmbiguousReferences",
                        "PlanParseFailure",
                        "ConfirmationRejected",
                    )
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="Context cache and dispatch",
                left=[
                    m(n, "Sum")
                    for n in ("DeviceCacheHit", "DeviceCacheMiss", "ActionsDispatched", "DispatchFailures")
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="Model tokens and tool calls",
                left=[m(n, "Sum") for n in ("ModelInputTokens", "ModelOutputTokens")],
                right=[m("AgentToolCalls", "Sum")],
                width=12,
            ),
            cw.GraphWidget(
                title="Lambda errors",
                left=[f.metric_errors(period=Duration.minutes(1)) for f in (orch, platform, state)],
                width=12,
            ),
            cw.GraphWidget(
                title="App API",
                left=[app_api.metric_client_error(), app_api.metric_server_error()],
                right=[app_api.metric_latency(statistic="p95")],
                width=24,
            ),
        )
