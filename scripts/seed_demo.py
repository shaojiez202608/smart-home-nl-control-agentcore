"""Load the synthetic demo homes into a deployed stack.

    uv run python scripts/seed_demo.py [--config config/sandbox.json]

Creates the demo users in the Cognito user pool with random passwords
(stored only in the git-ignored .demo/credentials.json), and writes homes,
devices, memberships and speakers to the simulated platform's tables.
Re-running resets device state to the sample data.
"""

from __future__ import annotations

import argparse
import json

import boto3
from common import (
    ROOT,
    load_credentials,
    random_password,
    region,
    save_credentials,
    stack_name_from_config,
    stack_outputs,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/sandbox.json")
    args = ap.parse_args()
    out = stack_outputs(stack_name_from_config(args.config))
    data = json.loads((ROOT / "sample-data" / "homes.json").read_text(encoding="utf-8"))
    idp = boto3.client("cognito-idp", region_name=region())
    ddb = boto3.resource("dynamodb", region_name=region())
    devices_t = ddb.Table(out["DevicesTable"])
    members_t = ddb.Table(out["MembersTable"])
    speakers_t = ddb.Table(out["SpeakersTable"])

    try:
        creds = load_credentials()
    except FileNotFoundError:
        creds = {}
    subs: dict[str, str] = {}
    for home in data["homes"]:
        for m in home["members"]:
            user = m["username"]
            try:
                idp.admin_create_user(UserPoolId=out["UserPoolId"], Username=user, MessageAction="SUPPRESS")
            except idp.exceptions.UsernameExistsException:
                pass
            creds.setdefault(user, random_password())
            idp.admin_set_user_password(
                UserPoolId=out["UserPoolId"], Username=user, Password=creds[user], Permanent=True
            )
            attrs = idp.admin_get_user(UserPoolId=out["UserPoolId"], Username=user)["UserAttributes"]
            subs[user] = next(a["Value"] for a in attrs if a["Name"] == "sub")
            members_t.put_item(Item={"userId": subs[user], "homeId": home["homeId"], "role": m["role"]})
        for d in home["devices"]:
            devices_t.put_item(Item={**d, "homeId": home["homeId"], "aliases": d.get("aliases", [])})
    for s in data["speakers"]:
        speakers_t.put_item(
            Item={"speakerId": s["speakerId"], "homeId": s["homeId"], "ownerUserId": subs[s["ownerUsername"]]}
        )
    save_credentials(creds)
    print(f"Seeded {sum(len(h['devices']) for h in data['homes'])} devices, users: {', '.join(subs)}")
    print("Demo passwords are in .demo/credentials.json (git-ignored).")


if __name__ == "__main__":
    main()
