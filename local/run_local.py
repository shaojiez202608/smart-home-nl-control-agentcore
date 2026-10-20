"""Run the full request pipeline on your machine.

    uv run python -m local.run_local "我要睡觉了"                 # offline, rule-based planner
    uv run python -m local.run_local --agent bedrock "我要睡觉了" # real model via Amazon Bedrock

Offline mode needs no AWS access. Bedrock mode uses your local AWS
credentials to call the model directly (no AgentCore Runtime, Memory or
Gateway involved); the cache, platform and memory are in-memory fakes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("src/core", "src/agent", "."):
    sys.path.insert(0, str(ROOT / p))

from smarthome_core.orchestrator import Orchestrator  # noqa: E402
from smarthome_core.ports import AgentRequest  # noqa: E402

from local.fakes import FakePlatform, FakePreferenceStore, MemoryCache, RuleBasedAgent  # noqa: E402


class LocalBedrockAgent:
    """Calls the same planner code the AgentCore Runtime runs, in-process."""

    def __init__(self, platform: FakePlatform, prefs: FakePreferenceStore):
        self.platform = platform
        self.prefs = prefs
        self.last_usage: dict = {}

    def plan(self, request: AgentRequest) -> str:
        from agent_app.planner import run_plan

        out = run_plan(
            utterance=request.utterance,
            devices=request.devices,
            preferences=request.preferences,
            history=[],
            channel=request.channel,
            status_lookup=lambda uuid: self.platform.devices[uuid].state,
        )
        self.last_usage = out["usage"]
        return json.dumps(out["plan"], ensure_ascii=False)


def build(agent_kind: str):
    platform = FakePlatform()
    prefs = FakePreferenceStore()
    agent = LocalBedrockAgent(platform, prefs) if agent_kind == "bedrock" else RuleBasedAgent()
    orch = Orchestrator(
        cache=MemoryCache(), directory=platform, agent=agent, dispatcher=platform, preferences=prefs
    )
    return orch, platform, prefs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("utterances", nargs="+")
    ap.add_argument("--agent", choices=["mock", "bedrock"], default="mock")
    ap.add_argument("--channel", choices=["app", "speaker"], default="app")
    args = ap.parse_args()
    orch, platform, prefs = build(args.agent)
    for u in args.utterances:
        resp = orch.handle("local-user", u, channel=args.channel)
        print(f"\n>>> {u}")
        print(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))
    print("\nPreferences stored:", [r.text for r in prefs.for_user("local-user")])


if __name__ == "__main__":
    main()
