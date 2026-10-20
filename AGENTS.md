# Notes for AI coding assistants

* This is a prototype (not for production). Keep the repository free of customer, company, brand or person
  names, real data, domains and account IDs (use `<ACCOUNT_ID>` / `<REGION>`). Sample data is synthetic.
* Layout: `src/core` (cloud-agnostic domain logic, stdlib only), `src/agent` (agent, direct code deployment), `src/aws`
  (adapters), `src/functions` (Lambda handlers), `infra` (CDK, Python), `config` (all parameters), `local`
  (fakes), `scripts`, `sample-data`, `tests`.
* Safety rules live in code (`validation.py`, `policy.py`, `ambiguity.py`, `memory_policy.py`), never only in
  prompts. Any change to them needs unit tests and, where relevant, evaluation cases.
* Before committing: `make lint test eval synth scan`. `synth` must report no unacknowledged cdk-nag findings;
  every acknowledgement needs a written reason in `infra/smarthome_infra/nag.py` and `SECURITY-NOTES.md`.
* Statements about AWS service behaviour must be checked against official documentation and recorded in the
  fact-check table in `docs/architecture.md`.
* Do not change account-level settings. Deploy only to non-production accounts and destroy after testing;
  record runs in `DEPLOYMENT-LOG.md` and results in `VERIFICATION.md`.

## Boundaries
* **Always:** run `make lint test eval synth scan` before committing; keep safety rules in code with tests;
  record what was actually run in `VERIFICATION.md`; use placeholders (`<ACCOUNT_ID>`, `111122223333`, example.com).
* **Ask first:** deploying to any AWS account; adding a cdk-nag acknowledgement; changing IAM, resource policies,
  the Cedar policy or the sensitive-action table; adding dependencies.
* **Never:** commit credentials, account ids or customer data; change account-level settings; push to a remote;
  weaken a validation or relax an evaluation expectation to make a test pass.
