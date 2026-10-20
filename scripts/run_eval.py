"""Run the synthetic evaluation set against the full application pipeline.

    uv run python scripts/run_eval.py                  # mock model (deterministic, offline)
    uv run python scripts/run_eval.py --agent bedrock  # real model via Amazon Bedrock

Each case starts from a fresh synthetic home. The script prints a pass rate
and the details of every failed check, and writes a JSON report.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("src/core", "src/agent", "."):
    sys.path.insert(0, str(ROOT / p))

from smarthome_core.messages import FALLBACK_REPLY  # noqa: E402
from smarthome_core.orchestrator import Orchestrator  # noqa: E402

from local.fakes import FakePlatform, FakePreferenceStore, MemoryCache, ScriptedAgent  # noqa: E402
from local.run_local import LocalBedrockAgent  # noqa: E402

CASES = ROOT / "sample-data" / "eval" / "cases.json"


class RecordingLog:
    """Captures orchestrator warnings so rejection reasons can be asserted."""

    def __init__(self):
        import logging

        self.records: list[dict] = []
        outer = self

        class H(logging.Handler):
            def emit(self, record):
                outer.records.append(record.__dict__)

        self.handler = H()
        logging.getLogger("smarthome_core.orchestrator").addHandler(self.handler)

    def rejection_reasons(self) -> list[str]:
        out = []
        for r in self.records:
            for rej in r.get("rejected", []) or []:
                out.append(rej["reason"])
        return out


def run_case(case: dict, agent_kind: str) -> tuple[list[str], dict]:
    platform = FakePlatform()
    prefs = FakePreferenceStore()
    if agent_kind == "mock":
        agent = ScriptedAgent([case["model_output"]])
    else:
        agent = LocalBedrockAgent(platform, prefs)
    orch = Orchestrator(cache=MemoryCache(), directory=platform, agent=agent, dispatcher=platform, preferences=prefs)
    log = RecordingLog()
    t = time.perf_counter()
    resp = orch.handle("eval-user", case["utterance"], channel=case.get("channel", "app"))
    elapsed = time.perf_counter() - t
    exp = case["expect"]
    dispatched = [(a.uuid, a.action) for _, a in platform.dispatched]
    held = [(a["uuid"], a["action"]) for a in (resp.pending_confirmation or {}).get("actions", [])]
    skipped = [s["uuid"] for s in resp.skipped]
    fails: list[str] = []

    if "type" in exp and resp.type != exp["type"]:
        fails.append(f"type: expected {exp['type']}, got {resp.type}")
    if "dispatched_exact" in exp and sorted(dispatched) != sorted(tuple(x) for x in exp["dispatched_exact"]):
        fails.append(f"dispatched: expected {exp['dispatched_exact']}, got {dispatched}")
    for x in exp.get("dispatched_include", []):
        if tuple(x) not in dispatched:
            fails.append(f"missing dispatch {x}")
    for x in exp.get("never_dispatched", []):
        if tuple(x) in dispatched:
            fails.append(f"forbidden dispatch {x}")
    for x in exp.get("held_include", []):
        if tuple(x) not in held:
            fails.append(f"expected held for confirmation {x}, held={held}")
    for u in exp.get("skipped_or_held_include", []):
        if u not in skipped and u not in [h[0] for h in held]:
            fails.append(f"{u} neither skipped nor held (skipped={skipped})")
    for reason in exp.get("rejected_include", []):
        if not any(reason in r for r in log.rejection_reasons()):
            fails.append(f"expected rejection containing {reason!r}, got {log.rejection_reasons()}")
    if "preferences_written" in exp and len(prefs.records) != exp["preferences_written"]:
        fails.append(f"preferences written: expected {exp['preferences_written']}, got {len(prefs.records)}")
    if exp.get("message_is_fallback") and resp.message != FALLBACK_REPLY:
        fails.append("message is not the fixed fallback")
    if "message_excludes" in exp and exp["message_excludes"] in resp.message:
        fails.append("model-written execution claim leaked into the reply")
    pb = exp.get("param_below_current")
    if pb:
        vals = [a.params.get(pb["param"]) for _, a in platform.dispatched if a.uuid == pb["uuid"]]
        if not vals or not all(isinstance(v, int) and v < pb["current"] for v in vals):
            fails.append(f"{pb['param']} not below current {pb['current']}: {vals}")

    return fails, {"type": resp.type, "message": resp.message, "dispatched": dispatched, "held": held,
                   "skipped": skipped, "seconds": round(elapsed, 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["mock", "bedrock"], default="mock")
    ap.add_argument("--report", default=str(ROOT / ".build" / "eval-report.json"))
    args = ap.parse_args()
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.agent != "mock":
        cases = [c for c in cases if not c.get("mock_only")]
    results = []
    for case in cases:
        fails, observed = run_case(case, args.agent)
        results.append({"id": case["id"], "category": case["category"], "passed": not fails, "failures": fails,
                        "observed": observed})
        print(f"{'PASS' if not fails else 'FAIL'}  {case['id']:<28} {case['category']}")
        for f in fails:
            print(f"      - {f}")
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed ({passed / len(results):.0%}) with --agent {args.agent}")
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps({"agent": args.agent, "passed": passed, "total": len(results),
                                             "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
