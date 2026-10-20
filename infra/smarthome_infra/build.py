"""Builds deployment packages locally with uv; no Docker or container runtime is needed.

* The shared Lambda layer contains the cloud-agnostic core, the AWS adapters and
  pinned third-party dependencies, including a recent boto3: the Lambda
  runtime's bundled SDK may predate the AgentCore APIs used here.
* The agent package is an AgentCore Runtime direct code deployment: ARM64
  wheels and the agent source at the root of one directory, which CDK zips and
  uploads as an S3 asset.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / ".build"
LAYER_REQUIREMENTS = ["redis==8.1.0", "boto3==1.43.115"]


AGENT_PYTHON = "3.13"  # matches the PYTHON_3_13 runtime of the agent
AGENT_ENTRY = """from agent_app.main import app

if __name__ == "__main__":
    app.run()
"""


def _uv_install(target: Path, python_version: str, args: list[str]) -> None:
    subprocess.run(  # noqa: S603
        [
            shutil.which("uv") or "uv",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(target),
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            python_version,
            "--only-binary",
            ":all:",
            *args,
        ],
        check=True,
    )


def build_layer() -> Path:
    out = BUILD / "layer"
    site = out / "python"
    if out.exists():
        shutil.rmtree(out)
    site.mkdir(parents=True)
    _uv_install(site, "3.14", LAYER_REQUIREMENTS)
    for pkg in ("src/core/smarthome_core", "src/aws/smarthome_aws"):
        shutil.copytree(ROOT / pkg, site / Path(pkg).name, ignore=shutil.ignore_patterns("__pycache__"))
    return out


def build_agent_package() -> Path:
    out = BUILD / "agent"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    agent = ROOT / "src" / "agent"
    _uv_install(out, AGENT_PYTHON, ["-r", str(agent / "requirements.txt")])
    shutil.copytree(agent / "agent_app", out / "agent_app", ignore=shutil.ignore_patterns("__pycache__"))
    (out / "main.py").write_text(AGENT_ENTRY, encoding="utf-8")
    # The runtime needs 644 for files and 755 for directories and executables.
    for f in out.rglob("*"):
        if "__pycache__" in f.parts:
            continue
        f.chmod(0o755 if f.is_dir() or f.parent.name == "bin" else 0o644)
    for cache in list(out.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    return out
