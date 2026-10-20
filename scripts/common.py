"""Helpers shared by the deployment-side scripts (seed, demo, e2e)."""

from __future__ import annotations

import json
import os
import secrets
import string
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = ROOT / ".demo"  # git-ignored; holds generated demo passwords
CRED_FILE = DEMO_DIR / "credentials.json"


def region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def stack_outputs(stack_name: str) -> dict[str, str]:
    cfn = boto3.client("cloudformation", region_name=region())
    stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack.get("Outputs", [])}


def stack_name_from_config(path: str = "config/sandbox.json") -> str:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))["stackName"]


def random_password() -> str:
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(20))
    return core + "aA1!"  # satisfy every character class of the pool policy


def load_credentials() -> dict[str, str]:
    return json.loads(CRED_FILE.read_text(encoding="utf-8"))


def save_credentials(creds: dict[str, str]) -> None:
    DEMO_DIR.mkdir(exist_ok=True)
    CRED_FILE.write_text(json.dumps(creds, indent=2), encoding="utf-8")
    CRED_FILE.chmod(0o600)


def id_token(outputs: dict[str, str], username: str) -> str:
    creds = load_credentials()
    idp = boto3.client("cognito-idp", region_name=region())
    resp = idp.initiate_auth(
        ClientId=outputs["AppClientId"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": creds[username]},
    )
    return resp["AuthenticationResult"]["IdToken"]
