#!/usr/bin/env bash
# Canary for the secret scanner: plant a freshly generated fake AWS access key in a throw-away
# directory outside the repository and confirm that gitleaks (with this repository's configuration)
# reports it. Nothing is written into the repository. Exits 1 if the scanner misses it.
set -uo pipefail
cd "$(dirname "$0")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
KEY="AKIA$(LC_ALL=C tr -dc 'A-Z2-7' < /dev/urandom | head -c 16)"
SECRET="$(LC_ALL=C tr -dc 'A-Za-z0-9/+' < /dev/urandom | head -c 40)"
printf 'aws_access_key_id = %s\naws_secret_access_key = %s\n' "$KEY" "$SECRET" > "$TMP/creds.ini"
if gitleaks dir "$TMP" --config .gitleaks.toml --no-banner --redact >/dev/null 2>&1; then
  echo "secret canary NOT detected: the scanner configuration is broken" >&2
  exit 1
fi
echo "secret canary detected by gitleaks (expected)"
