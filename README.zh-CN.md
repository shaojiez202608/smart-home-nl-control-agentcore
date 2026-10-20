# NL_control：基于 Amazon Bedrock AgentCore 的智能家居自然语言控制

> **Prototype，不用于生产。** 只部署在非生产账号。继续使用之前，请先看[上生产前需要加固的清单](docs/handover.md#production-hardening-checklist)。

[English](README.md)

这是一个参考实现：在**不改动**既有智能家居平台的设备接入、权限体系和指令下发的前提下，增加一条「自然语言 → 结构化计划 → 校验 → 下发」的链路。Amazon Bedrock 上的轻量模型只负责解释意图；允许做什么由确定性代码判断，执行仍走既有控制平面。

## 包含的内容
* **既有控制平面不动**：Agent 只返回计划，编排器通过平台既有的 REST API 下发。Agent 唯一的工具（经 AgentCore Gateway）是只读的状态查询。
* **上下文预加载**：设备清单、可用指令集和状态随请求一起交给 Agent，控制类请求只需一次模型调用。
* **代码级硬校验**：UUID 白名单、指令与参数范围、矛盾动作、动作数量上限、非法输出走固定兜底文案。
* **执行层敏感动作策略**：按动作定义（开锁、撤防），一次性且绑定用户的二次确认；语音发起的敏感动作挂起，需在 App 中确认。
* **同名设备歧义拦截**：App 中的设备名和别名是唯一权威来源。
* **AgentCore Memory 写入治理**：只存用户明确给出的偏好，同一设备同类属性只保留最新一条，自动过期。
* **回复文案由控制平面的受理结果生成**，设备执行结果通过指令回执异步确认。
* **合成评测集**覆盖以上全部行为，可离线运行，也可接真实模型运行。

## 快速开始

```bash
uv sync --all-groups
uv run pytest                               # 单元测试
uv run python scripts/run_eval.py           # 评测集，mock 模型，不需要 AWS
uv run python -m local.run_local "我要睡觉了" "帮我把前门锁打开"

# 部署到非生产账号（需要 Docker、Node.js 和 CDK bootstrap）
npx aws-cdk@latest deploy -c config=config/sandbox.json --require-approval never
uv run python scripts/seed_demo.py
uv run python scripts/e2e_test.py
npx aws-cdk@latest destroy -c config=config/sandbox.json --force
```

## 文档
| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 架构图、请求链路、硬校验、记忆规则、可观测、局限、事实核对 |
| [docs/deployment-guide.md](docs/deployment-guide.md) | 前置条件、一条命令部署、全部参数说明 |
| [docs/demo-script.md](docs/demo-script.md) | 演示脚本 |
| [docs/handover.md](docs/handover.md) | 对接真实平台、上生产前的加固清单 |
| [docs/cleanup.md](docs/cleanup.md) | 销毁与残留核对 |
| [docs/prior-art.md](docs/prior-art.md) | 相近资产与差异点 |
| [DECISIONS.md](DECISIONS.md) / [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | 设计决策；待确认的业务问题 |
| [SECURITY-NOTES.md](SECURITY-NOTES.md) / [THREAT_MODEL.md](THREAT_MODEL.md) | 安全控制、cdk-nag 抑制理由、威胁模型 |
| [VERIFICATION.md](VERIFICATION.md) / [DEPLOYMENT-LOG.md](DEPLOYMENT-LOG.md) | 实际运行了什么、结果如何 |

## 成本
计费维度：Amazon Bedrock 模型 token、AgentCore Runtime / Memory / Gateway 用量、ElastiCache Serverless、NAT 网关、Lambda、API Gateway、AWS WAF、AWS IoT Core 消息、DynamoDB、CloudWatch。价格以官方页面为准，例如 [Amazon Bedrock 定价](https://aws.amazon.com/bedrock/pricing/)、[Amazon Bedrock AgentCore 定价](https://aws.amazon.com/bedrock/agentcore/pricing/)。

## 区域
本项目只面向海外区域。AgentCore 的官方区域列表中没有中国区域，详见 [architecture.md 的事实核对](docs/architecture.md#fact-check)。

## 安全与许可
安全问题上报方式见 [SECURITY.md](SECURITY.md)。许可证为 MIT-0，见 [LICENSE](LICENSE)。
