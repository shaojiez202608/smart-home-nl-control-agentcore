#!/usr/bin/env bash
# Guarded deploy: preflight -> cdk diff (refuses deletions/replacements unless ALLOW_DESTRUCTIVE=1)
# -> cdk deploy -> keep the deployed asset manifest (cleanup deletes exactly those bootstrap objects).
# Output is masked (standalone 12-digit numbers). Usage: scripts/deploy.sh [config-file]
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIG="${1:-config/sandbox.json}"
CDK_VERSION="${CDK_VERSION:-2.1146.0}"
CDK=(npx -y "aws-cdk@${CDK_VERSION}")
mask() { sed -E 's/(^|[^0-9A-Za-z])[0-9]{12}([^0-9A-Za-z]|$)/\1111122223333\2/g'; }

uv run python scripts/preflight.py --config "$CONFIG"
STACK=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["stackName"])' "$CONFIG")

echo "== cdk diff"
DIFF=$("${CDK[@]}" diff -c "config=$CONFIG" "$STACK" 2>&1 || true)
echo "$DIFF" | mask | grep -vE '^\s*$' | tail -80
if echo "$DIFF" | grep -qE '^\[-\]|requires replacement|may be replaced|\(replace\)'; then
  if [ "${ALLOW_DESTRUCTIVE:-}" != "1" ]; then
    echo "The change set deletes or replaces resources. Review the diff above and re-run with ALLOW_DESTRUCTIVE=1." >&2
    exit 2
  fi
  echo "ALLOW_DESTRUCTIVE=1: continuing with deletions/replacements."
fi

echo "== cdk deploy"
"${CDK[@]}" deploy -c "config=$CONFIG" "$STACK" --require-approval never 2>&1 | mask
mkdir -p .deploy
cp "cdk.out/${STACK}.assets.json" ".deploy/${STACK}.$(date -u +%Y%m%dT%H%M%SZ).assets.json"
cp "cdk.out/${STACK}.template.json" ".deploy/${STACK}.$(date -u +%Y%m%dT%H%M%SZ).template.json"
echo "Deployed. Next: make seed, then ALLOW_PAID=1 make verify."
