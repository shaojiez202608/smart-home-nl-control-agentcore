#!/usr/bin/env bash
# Destroy the stack and remove what CloudFormation leaves behind. Asks twice: once for the stack, and
# once more before deleting data that the handover profile retains or this project's objects in the
# shared CDK bootstrap bucket. A failing step prints a warning and the script continues.
# Non-interactive: CONFIRM_STACK=<stack name> and CONFIRM_DATA=yes.
# Usage: scripts/cleanup.sh [config-file] [extra regions ...]
set -uo pipefail
cd "$(dirname "$0")/.."
CONFIG="${1:-config/sandbox.json}"; shift || true
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
REGIONS=("$REGION" "$@")
CDK_VERSION="${CDK_VERSION:-2.1146.0}"
mask() { sed -E 's/(^|[^0-9A-Za-z])[0-9]{12}([^0-9A-Za-z]|$)/\1111122223333\2/g'; }
warn() { echo "WARNING: $* (continuing)" >&2; }
read -r STACK PREFIX REMOVAL < <(python3 -c '
import json, sys
c = json.load(open(sys.argv[1]))
print(c["stackName"], c["resourcePrefix"], c["removalPolicy"])' "$CONFIG")
UP="${PREFIX//-/_}"
ask() {  # ask <prompt> <expected> <env value>
  [ "${3:-}" = "$2" ] && return 0
  [ -t 0 ] || { echo "Not confirmed (set the matching CONFIRM_* variable)."; return 1; }
  read -r -p "$1 " a; [ "$a" = "$2" ]
}

ask "Type the stack name ($STACK) to destroy it in $REGION:" "$STACK" "${CONFIRM_STACK:-}" || exit 1

echo "== cdk destroy"
npx -y "aws-cdk@${CDK_VERSION}" destroy -c "config=$CONFIG" "$STACK" --force 2>&1 | mask || warn "cdk destroy failed"

echo "== log groups created by services outside the stack (runtime, CDK provider functions)"
# "$STACK" catches stack log groups that a function recreated while the stack was being deleted.
for p in "/aws/bedrock-agentcore/runtimes/$UP" "/aws/lambda/$STACK" "$STACK"; do
  for g in $(aws logs describe-log-groups --region "$REGION" --log-group-name-prefix "$p" --query 'logGroups[].logGroupName' --output text 2>/dev/null); do
    [ "$g" = "None" ] && continue
    aws logs delete-log-group --region "$REGION" --log-group-name "$g" && echo "deleted $g" || warn "could not delete $g"
  done
done

echo "== workload identities named after the runtime or gateway"
for w in $(aws bedrock-agentcore-control list-workload-identities --region "$REGION" --query "workloadIdentities[?starts_with(name, '$UP') || starts_with(name, '$PREFIX')].name" --output text 2>/dev/null); do
  [ "$w" = "None" ] && continue
  aws bedrock-agentcore-control delete-workload-identity --region "$REGION" --name "$w" && echo "deleted $w" || warn "could not delete $w"
done

if [ "$REMOVAL" = "retain" ]; then
  echo "removalPolicy=retain: tables, cache and memory were kept by CloudFormation and are listed by make verify-cleanup;"
  echo "delete them by name after exporting what you need (docs/cleanup.md)."
fi

echo "== this project's objects in the shared CDK bootstrap bucket (identified by asset hash)"
LIST=$(mktemp)
uv run python scripts/bootstrap_assets.py --regions "${REGIONS[@]}" --json "$LIST" | tail -n +1 | grep -E '^[a-z]{2}-' || warn "could not list bootstrap objects"
COUNT=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(sum(len(r["objects"]) for r in d.values()))' "$LIST" 2>/dev/null || echo 0)
if [ "$COUNT" != "0" ] && ask "Delete these $COUNT object versions (CDK generic provider packages are kept)? Type yes:" "yes" "${CONFIRM_DATA:-}"; then
  ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
  python3 -c '
import json, sys
for region, r in json.load(open(sys.argv[1])).items():
    for o in r["objects"]:
        print(region, o["Key"], o["VersionId"])' "$LIST" | while read -r R K V; do
    aws s3api delete-object --region "$R" --bucket "cdk-hnb659fds-assets-${ACCOUNT}-${R}" --key "$K" --version-id "$V" >/dev/null \
      && echo "deleted $R $K ($V)" || warn "could not delete $R $K ($V)"
  done
else
  echo "Kept bootstrap objects."
fi
echo "Now run: make verify-cleanup CONFIG=$CONFIG"
