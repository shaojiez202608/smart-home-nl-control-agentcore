# NL_control：基于 Amazon Bedrock AgentCore 的智能家居自然语言控制

> **Prototype，不用于生产。** 本项目是用于演示和评估的示例代码，只部署在可随时丢弃的非生产账号。使用者对部署、安全、运维和费用负责；继续使用之前，请与你的安全和法务团队评估（参见 [AWS 责任共担模型](https://aws.amazon.com/compliance/shared-responsibility-model/)），并先看[上生产前需要加固的清单](docs/handover.md#production-hardening-checklist)。

[English](README.md)

这是一个参考实现：在**不改动**既有智能家居平台的设备接入、权限体系和指令下发的前提下，增加一条「自然语言 → 结构化计划 → 校验 → 下发」的链路。Amazon Bedrock 上的轻量模型只负责解释意图；允许做什么由确定性代码判断，执行仍走既有控制平面。

包含的内容：
* **既有控制平面不动**：Agent 只返回计划，编排器通过平台既有的 REST API 下发。Agent 唯一的工具（经 AgentCore Gateway）是只读的状态查询，Gateway 上默认拒绝的 Cedar 策略从外部保证这一点。
* **上下文预加载**：控制类请求只需一次模型调用。
* **代码级硬校验**：UUID 白名单、指令与参数范围、矛盾动作、动作数量上限；模型输出非法或模型出错时返回固定兜底文案。
* **执行层敏感动作策略**（开锁、撤防）：一次性、绑定用户的二次确认；语音发起的敏感动作挂起，需在 App 中确认。
* **同名设备歧义拦截**、**确定性的记忆写入规则**、**回复文案由控制平面的受理结果生成**，设备执行结果通过指令回执异步确认。
* **合成评测集**覆盖以上全部行为，可离线运行，也可接真实模型运行。

## 架构
组件图、请求时序、信任边界、设计取舍和事实核对见 [docs/architecture.md](docs/architecture.md)；威胁模型见 [THREAT_MODEL.md](THREAT_MODEL.md)。

## 前置条件与成本
* 一个**可随时丢弃的非生产** AWS 账号，所选区域可用 Amazon Bedrock AgentCore 和所配置的模型，并已完成 CDK bootstrap。
* Python 3.12+ 与 [uv](https://docs.astral.sh/uv/)、Node.js 20+、AWS CLI v2。不需要 Docker：Agent 以包含 ARM64 依赖的 zip 包部署（AgentCore Runtime 直接代码部署）。详见 [docs/deployment-guide.md](docs/deployment-guide.md)。
* **成本**：所部署资源的费用由使用者承担。计费维度包括模型 token、AgentCore 用量、ElastiCache、NAT 网关、Lambda、API Gateway、AWS WAF、AWS IoT Core、DynamoDB、Cognito、Secrets Manager、CloudWatch，价格以各服务的官方定价页为准（链接见[英文 README](README.md#prerequisites-and-costs)）。不假设任何免费额度。NAT 网关和无服务器缓存在栈存在期间持续计费，测试完请及时销毁。

## 部署
```bash
uv sync --all-groups
make preflight          # 只读检查：账号/区域、工具链、模型、配额；账号不对就停止
make deploy             # 会先运行 preflight；参数在 config/sandbox.json
make seed               # 写入合成的演示用户和家庭
```
在非生产账号中使用 `CONFIG=config/handover.json`（保留数据存储），见 [docs/handover.md](docs/handover.md)。

## 使用与验证
```bash
uv run python scripts/demo.py say "我要睡觉了"
make e2e                # 栈部署成功不代表应用可用；这一步也验证各项控制
```
演示脚本见 [docs/demo-script.md](docs/demo-script.md)。实际运行了什么、结果如何（含区域、模型和提交哈希）见 [VERIFICATION.md](VERIFICATION.md) 和 [DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md)。

## 故障排查
* `make preflight` 提示 VPC 或弹性 IP 不足：换一个区域或释放容量；本栈需要 1 个 VPC，每个 NAT 网关需要 1 个弹性 IP。
* 资产构建失败：`uv` 需要能访问 PyPI 下载 ARM64 wheel；没有 ARM64 wheel 的包不能用在 Agent 里（见 [DECISIONS.md](DECISIONS.md) D26）。
* 请求返回兜底文案：在编排器日志中查找 `agent_invocation_failed` 或 `plan_parse_failed`（模型原文只记录在这里，不会返回给用户）。

## 安全与局限
见 [SECURITY.md](SECURITY.md)（部署了什么、数据处理、各项控制及其实现位置、已知注意事项）、[SECURITY-NOTES.md](SECURITY-NOTES.md)（cdk-nag 抑制理由）以及 [docs/architecture.md](docs/architecture.md#limits-and-known-gaps) 中的局限。待确认的业务问题见 [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md)，设计决策见 [DECISIONS.md](DECISIONS.md)。

## 清理
```bash
make destroy            # 会要求确认
make verify-cleanup     # 只读残留核对
```
不会被自动删除的：栈删除后由服务创建的日志组、共享 CDK bootstrap S3 桶中的资产对象（Agent 代码包、Lambda 代码），以及使用 handover 配置时保留的数据存储。不要以为命令执行完就删干净了，详见 [docs/cleanup.md](docs/cleanup.md)。

## 目录结构
见[英文 README 的 Repository layout](README.md#repository-layout)。

## 本地测试
不需要 AWS 账号：
```bash
make lint test eval     # ruff、单元/适配器/配置测试、mock 模型评测集
make local              # 用内存假实现和规则规划器跑通全链路
make synth              # 带 cdk-nag 的 CloudFormation synth
```

## 贡献、安全与许可
贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。安全问题上报方式见 [SECURITY.md](SECURITY.md)。本项目采用 MIT-0 许可证，见 [LICENSE](LICENSE)；第三方依赖的许可证见 [THIRD-PARTY-LICENSES.md](THIRD-PARTY-LICENSES.md)。
