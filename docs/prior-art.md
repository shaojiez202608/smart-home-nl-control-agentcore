# Prior-art search

Search date: 2026-10-20.

## Method

* GitHub search (API) over the `aws-samples` and `awslabs` organisations and public repositories, with the
  queries `agentcore smart home`, `agentcore iot`, `bedrock iot natural language`, `strands agents smart home`,
  `agentcore device control`, `nova iot`, `llm smart home mqtt aws`.
* File-tree search of the official AgentCore samples repository (`awslabs/agentcore-samples`, about ten
  thousand files) for paths matching `iot`, `smart home`, `home automation`, `thermostat`, `appliance`.
* AWS documentation for AgentCore Runtime, Gateway, Identity and Memory (see the fact-check table in
  [architecture.md](architecture.md)).

## Closest assets

| Asset | What it covers | What it does not cover |
|---|---|---|
| [aws-samples/sample-smart-home-assistant-agent-on-agentcore](https://github.com/aws-samples/sample-smart-home-assistant-agent-on-agentcore) | A large "agent harness" platform using a smart-home scenario: orchestrator plus specialist agents on AgentCore Runtime, voice (Nova Sonic), admin console, per-user tool permissions with Cedar policies, skills catalogue, AgentCore Memory with built-in strategies, simulated IoT devices. Its design notes also describe stating devices up front instead of a discovery tool call, and a server-side `validate_command` that drops undeclared parameters. | The agent executes device commands itself through Gateway tools. There is no pattern for keeping an existing device-control platform unchanged with the model producing a plan only; no per-device-UUID whitelist; no per-action sensitive-action policy with a second confirmation (its devices are LED, rice cooker, fan, oven - no locks or alarm systems); no code-level same-name ambiguity guard; long-term memory uses model-driven extraction strategies rather than deterministic write rules; no reply generated from control-plane acceptance results; no evaluation set targeted at the hard checks. |
| [awslabs/agentcore-samples](https://github.com/awslabs/agentcore-samples) | Official feature samples for Runtime, Gateway (OpenAPI, Lambda, MCP targets), Identity (inbound JWT, outbound OAuth / API key), Memory, Observability. | No IoT or smart-home sample was found in the repository tree. The samples demonstrate individual features rather than an integration pattern around an existing control plane. |
| Community repositories found by the search (multi-agent smart-home assistants built with Strands; a robotics sample using AWS IoT and AgentCore; an IoT data pipeline with a Strands agent on AgentCore) | Agents that call device or data tools directly; multi-agent routing; data analytics on IoT telemetry. | Same gap as above: the agent is the executor. None separates intent interpretation from execution, and none ships deterministic validation, sensitive-action confirmation or memory write governance. |

## Differentiation (why this repository is not a duplicate)

1. **Existing control plane stays untouched.** The model only produces a structured plan. Authorisation,
   command validation and dispatch remain in the (simulated) existing platform, reached through its unchanged
   REST API. The agent has no tool that changes device state.
2. **Hard-check layer in application code**, independent of the prompt: UUID whitelist from the preloaded
   context, command and parameter range checks from the capability catalogue, contradiction and duplicate
   handling, a cap on actions per request, and a fixed fallback for unparsable output.
3. **Execution-layer sensitive-action policy** defined per action (`lock.unlock`, `security_system.disarm`)
   with a single-use, user-bound confirmation token; voice requests are held and must be confirmed in the app.
4. **Code-level ambiguity guard** for same-named devices (authoritative names and app aliases), plus alias
   writes that must resolve to a permitted device and cannot shadow authoritative names.
5. **Deterministic memory write governance**: only stated taste parameters, one record per device and
   attribute (overwrite), configurable expiry.
6. **Reply text from acceptance results**, never from model prose, plus command receipts confirmed by the
   asynchronous state path.
7. **Evaluation set** for exactly these properties, runnable offline and against a real model.

Conclusion: clear differentiation exists; development continued.
