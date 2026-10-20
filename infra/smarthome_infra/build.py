"""Builds the shared Lambda layer locally with uv (no Docker needed for Lambdas).

The layer contains the cloud-agnostic core, the AWS adapters and pinned
third-party dependencies, including a recent boto3: the Lambda runtime's
bundled SDK may predate the AgentCore APIs used here.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / ".build"
LAYER_REQUIREMENTS = ["redis==8.1.0", "boto3==1.43.115"]


def build_layer() -> Path:
    out = BUILD / "layer"
    site = out / "python"
    if out.exists():
        shutil.rmtree(out)
    site.mkdir(parents=True)
    subprocess.run(  # noqa: S603
        [
            shutil.which("uv") or "uv",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(site),
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.14",
            "--only-binary",
            ":all:",
            *LAYER_REQUIREMENTS,
        ],
        check=True,
    )
    for pkg in ("src/core/smarthome_core", "src/aws/smarthome_aws"):
        shutil.copytree(ROOT / pkg, site / Path(pkg).name, ignore=shutil.ignore_patterns("__pycache__"))
    return out
