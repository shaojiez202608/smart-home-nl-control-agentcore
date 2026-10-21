"""Build the agent package and write its exact distribution versions for dependency scanning.

    uv run python scripts/agent_freeze.py   # writes .build/agent-freeze.txt

osv-scanner cannot resolve src/agent/requirements.txt to the versions that are actually packaged (it
resolves transitive dependencies on its own), so the scan uses the versions found in the built package.
"""

from __future__ import annotations

import email
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "infra"))

from smarthome_infra.build import build_agent_package  # noqa: E402


def main() -> int:
    out = build_agent_package()
    lines = []
    for d in sorted(out.glob("*.dist-info")):
        meta = email.message_from_string((d / "METADATA").read_text(encoding="utf-8", errors="ignore"))
        lines.append(f"{meta['Name']}=={meta['Version']}")
    target = ROOT / ".build" / "agent-freeze.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(lines)} distributions -> {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
