# Notes for AI coding assistants

## Role
You are a contributor to a prototype (not for production): a reference implementation of natural-language
smart-home control on Amazon Bedrock AgentCore. Keep changes small, tested and explainable; the maintainer
reviews every change. Keep the repository free of customer, company, brand or person names, real data, domains
and account IDs (use `111122223333`, `<UPPER_SNAKE>` placeholders and `example.com`). Sample data is synthetic.

## Layout
`src/core` (cloud-agnostic domain logic, stdlib only), `src/agent` (agent, direct code deployment), `src/aws`
(adapters), `src/functions` (Lambda handlers), `infra` (CDK, Python), `config` (parameter templates), `local`
(fakes), `scripts`, `sample-data`, `tests`, `docs`.

Safety rules live in code (`validation.py`, `policy.py`, `ambiguity.py`, `memory_policy.py`, `messages.py`), never
only in prompts.

## Common commands
```bash
make init                       # dependencies and local config copies
make lint test eval synth       # no AWS needed
make scan-ci                    # gitleaks canary + history, detect-secrets
make preflight                  # read-only AWS checks (asks to confirm the account)
```
Deploy, verify and destroy commands are in the README; paid commands need `ALLOW_PAID=1`, destructive change
sets need `ALLOW_DESTRUCTIVE=1`.

## Git workflow
* Topic branch per change; Conventional Commits (`feat(scope): ...`, `fix`, `docs`, `test`, `chore`, `ci`,
  `refactor`); the body says what changed, why, and how it was verified; end with the co-author trailer if an
  assistant wrote the change.
* Pull requests are squash-merged. Do not rewrite published history; do not push without the maintainer.

## Boundaries
* **Always:** run `make lint test eval synth` before committing; keep safety rules in code with unit tests and
  evaluation cases; record what was actually run in `VERIFICATION.md` (date, Region, configuration, commit,
  failures, what it does not prove); check AWS service statements against official documentation and record
  them in the fact-check table in `docs/architecture.md`.
* **Ask first:** deploying to any AWS account; adding a cdk-nag acknowledgement (it must be attached to a construct
  and carry a reason in `nag.py` and `SECURITY-NOTES.md`); changing IAM, trust or resource policies, the Cedar
  policy or the sensitive-action table; adding dependencies; deleting anything in a shared resource such as the
  CDK bootstrap bucket.
* **Never:** commit credentials, account ids or real data; change account-level settings; modify the CDKToolkit
  stack; push to a remote; weaken a validation or relax an evaluation expectation to make a test pass; bypass a
  tool permission prompt.
