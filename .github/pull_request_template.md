## What and why

## How it was verified
- [ ] `make lint test eval` passed (mock evaluation and negative control)
- [ ] `make synth` passed with no unacknowledged cdk-nag findings (new acknowledgements have written reasons)
- [ ] Changes to validation, policy, ambiguity or memory rules have unit tests and evaluation cases
- [ ] Deployed and ran `ALLOW_PAID=1 make verify`, then `make destroy` and `make verify-cleanup` (state the Region and commit), or explain why not
- [ ] `make scan-ci` passed (gitleaks canary and history, detect-secrets)

## What was not tested

## Checklist
- [ ] No customer, company or person names, real data, account ids or credentials
- [ ] Documentation updated (README, docs/, VERIFICATION.md, DECISIONS.md as applicable)

By submitting this pull request, I confirm that you can use, modify, copy, and redistribute this contribution,
under the terms of the project license.
