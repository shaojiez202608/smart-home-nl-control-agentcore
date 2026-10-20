# Common commands. Override CONFIG for the handover profile: make deploy CONFIG=config/handover.json
CONFIG ?= config/sandbox.json
CDK    := npx --yes aws-cdk@latest

.PHONY: install lint test eval eval-model local synth preflight deploy seed e2e destroy verify-cleanup scan

install:        ## install all dependency groups
	uv sync --all-groups

lint:
	uv run ruff check .

test:           ## unit and adapter tests (no AWS needed)
	uv run pytest

eval:           ## evaluation set with the mock model (no AWS needed)
	uv run python scripts/run_eval.py

eval-model:     ## evaluation set with the real model through Amazon Bedrock (credentials required)
	uv run python scripts/run_eval.py --agent bedrock

local:          ## offline demo of the full pipeline
	uv run python -m local.run_local "我要睡觉了" "帮我把前门锁打开"

synth:          ## CloudFormation synth with cdk-nag
	$(CDK) synth -c config=$(CONFIG) --quiet

preflight:      ## read-only checks: account/Region, tool chain, model, quotas, bootstrap
	uv run python scripts/preflight.py --config $(CONFIG)

deploy: preflight
	$(CDK) deploy -c config=$(CONFIG) --require-approval never

seed:           ## load synthetic demo data into a deployed stack
	uv run python scripts/seed_demo.py --config $(CONFIG)

e2e:            ## end-to-end verification against a deployed stack
	uv run python scripts/e2e_test.py --config $(CONFIG)

destroy:        ## asks for confirmation; deletes everything the stack owns (sandbox profile)
	$(CDK) destroy -c config=$(CONFIG)

verify-cleanup: ## read-only leftover check after destroy
	uv run python scripts/verify_cleanup.py --config $(CONFIG)

scan:           ## secret scan (detect-secrets)
	uvx detect-secrets scan --exclude-files '(\.venv|\.build|cdk\.out|\.git|\.demo)/'
