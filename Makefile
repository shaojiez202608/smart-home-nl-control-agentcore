# Single entry point. Targets used by CI (init, lint, test, eval, synth, scan-ci) only check: they never
# change tracked files or AWS resources. Targets that call AWS say so; paid ones need ALLOW_PAID=1.
# Use the handover profile with: make deploy CONFIG=config/handover.json
CONFIG      ?= config/sandbox.json
CDK_VERSION ?= 2.1146.0
CDK         := npx --yes aws-cdk@$(CDK_VERSION)
NO_AWS      := env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_PROFILE

.PHONY: help init install lint test eval eval-model local synth preflight deploy seed verify destroy verify-cleanup scan scan-ci

help:           ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

init:           ## install dependencies; create config/*.json from the templates if missing
	uv sync --all-groups
	@for f in sandbox handover; do [ -f config/$$f.json ] || { cp config/$$f.example.json config/$$f.json; echo "created config/$$f.json"; }; done

install: init

lint:           ## ruff lint and format check (no changes)
	uv run ruff check .
	uv run ruff format --check .

test:           ## unit, adapter and handler tests (no AWS)
	uv run pytest

eval:           ## evaluation set with the mock model, then the negative control (must be reported as failing)
	uv run python scripts/run_eval.py
	uv run python scripts/run_eval.py --negative-control

eval-model:     ## evaluation set with the real model through Amazon Bedrock (paid: ALLOW_PAID=1)
	uv run python scripts/run_eval.py --agent bedrock

local:          ## offline demo of the full pipeline
	uv run python -m local.run_local "我要睡觉了" "帮我把前门锁打开"

synth:          ## cdk synth + cdk-nag for both templates and a prefix variant, without AWS credentials
	$(NO_AWS) $(CDK) synth -c config=config/sandbox.example.json --quiet
	$(NO_AWS) $(CDK) synth -c config=config/handover.example.json --quiet -o .build/cdk.out.handover
	uv run python -c "import json; c=json.load(open('config/sandbox.example.json')); c.update(resourcePrefix='lab-x', stackName='lab-x-check', topicPrefix='home'); json.dump(c, open('.build/variant.json','w'))"
	$(NO_AWS) $(CDK) synth -c config=.build/variant.json --quiet -o .build/cdk.out.variant

preflight:      ## read-only: confirm account (last 4 digits) and Region, tools, model, AgentCore, bootstrap, quotas
	uv run python scripts/preflight.py --config $(CONFIG)

deploy:         ## preflight, refuse deletions/replacements unless ALLOW_DESTRUCTIVE=1, deploy
	./scripts/deploy.sh $(CONFIG)

seed:           ## load synthetic demo data into the deployed stack (AWS)
	uv run python scripts/seed_demo.py --config $(CONFIG)

verify:         ## end-to-end checks against the deployed stack (paid: ALLOW_PAID=1)
	uv run python scripts/e2e_test.py --config $(CONFIG)

destroy:        ## destroy the stack and remove leftovers, with confirmations (AWS)
	./scripts/cleanup.sh $(CONFIG)

verify-cleanup: ## read-only leftover check; exits non-zero if anything is left (AWS)
	uv run python scripts/verify_cleanup.py --config $(CONFIG)

scan-ci:        ## secret scans: gitleaks canary + full history, detect-secrets on the working tree
	./scripts/secret_canary.sh
	gitleaks git . --config .gitleaks.toml --no-banner --redact
	uvx detect-secrets scan --exclude-files '(\.venv|\.build|cdk\.out|\.git|\.demo|\.deploy|uv\.lock)' \
	  | python3 -c 'import json,sys; r=json.load(sys.stdin)["results"]; print("detect-secrets findings:", len(r)); sys.exit(1 if r else 0)'

scan: scan-ci   ## local scans: plus workflow lint and dependency audit
	uvx zizmor --offline .github/workflows
	osv-scanner scan source --lockfile uv.lock --lockfile src/agent/requirements.txt
