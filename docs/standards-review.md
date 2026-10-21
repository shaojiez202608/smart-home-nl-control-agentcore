# Standards review

Review of this repository against the general prototype checklist (sections A-F) and the AgentCore checklist
(items 1-17). "Before" is the state at the start of the 0.1.0 round (after the one-time history rewrite);
"After" is the state at tag `v0.1.0`. Status values: 满足 (met), 需修改 (needed changes), 不适用 (not applicable,
with the reason). Test and scan results are in [VERIFICATION.md](../VERIFICATION.md).

## Summary

| Checklist | Items | After: 满足 | After: 不适用 | After: 需修改 (open) |
|---|---|---|---|---|
| General A-F | 17 | 17 | 0 | 0 |
| AgentCore 1-17 | 17 | 16 | 1 (item 13; item 1's agent-side check) | 0 |

Partially open items that are recorded rather than fixed: ASH could not run with all scanners on this machine
(AgentCore item 16 below, VERIFICATION.md); CI was not run on GitHub, only executed locally with the same commands.

## A. Positioning and redaction

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| A1 | README first screen: disclaimer (demo/learning only, user responsible, security and legal review with the shared responsibility model) and link to the hardening checklist | 满足 | README.md:3-7 | 满足 | README.md:3-10 and README.zh-CN.md:3-10 now state the three points as a list and link `docs/handover.md#production-hardening-checklist` |
| A2 | grep for customer, person, domain, 12-digit numbers, profile names, `[INSERT` = 0 | 需修改 | An internal scanner name and an account-type description in VERIFICATION/DEPLOYMENT-LOG and history; a personal e-mail as author | 满足 | History rewritten once (authors, internal names, earlier prefixes, an earlier 12-digit CI placeholder); `git log --all -p` grep result 0 (VERIFICATION.md "Repository hygiene"); only `111122223333`, `example.com` and AWS/GitHub/PyPI domains remain |
| A3 | Placeholders `<UPPER_SNAKE>`, `example.com`, `111122223333`; screenshots with source; synthetic data | 需修改 | `<ACCOUNT_ID>` in AGENTS.md, docs/deployment-guide.md, scripts/verify_cleanup.py | 满足 | `<ACCOUNT_ID>` replaced (git grep returns 0); no screenshots in the repository (nothing to record); `sample-data/homes.json:2` synthetic |

## B. Repository files

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| B1 | LICENSE with copyright line; SECURITY (complete); CoC; CONTRIBUTING (tests, commit rules, no account ids) | 需修改 | LICENSE had no copyright line | 满足 | LICENSE:3 Amazon copyright line (MIT-0); SECURITY.md: reporting via AWS vulnerability page / aws-security@amazon.com, what is deployed, data handling, controls mapped to files, known considerations, 7 hardening steps, cleanup; CODE_OF_CONDUCT.md (Amazon Open Source CoC); CONTRIBUTING.md (make checks, Conventional Commits, squash merge, no account ids or real config, license confirmation, AI rule) |
| B2 | THIRD-PARTY-LICENSES in two parts; CHANGELOG (Keep a Changelog) + VERSION | 需修改 | Licence table claimed "agent container", listed mcp 2.1.1 | 满足 | THIRD-PARTY-LICENSES.md: "distributed with the repository" (none) and "downloaded at build or deploy time", licences read from wheel metadata on 2026-10-21, no-relicensing statement; CHANGELOG.md 0.1.0, VERSION `0.1.0`, tag `v0.1.0` |
| B3 | Issue/PR templates, AGENTS.md + CLAUDE.md, Makefile, `.gitignore` / `.dockerignore` | 需修改 | No CODEOWNERS; Makefile lacked `init`, `verify`; scan was detect-secrets only | 满足 | `.github/ISSUE_TEMPLATE/` (forms, blank issues off, security link), `pull_request_template.md` (checks run, what was not tested); `.github/CODEOWNERS` (@shaojiez202608: CI, scanning, AI config, nag.py, deploy/cleanup scripts); AGENTS.md (role, commands, git flow, Always/Ask first/Never), CLAUDE.md = `@AGENTS.md`; Makefile targets `init preflight lint test eval synth deploy seed verify destroy verify-cleanup scan scan-ci`; `.gitignore` excludes real config, `.deploy/`, `cdk.out`, `cdk.context.json`, `.demo`; `src/agent/.dockerignore` for the optional container path |
| B4 | DECISIONS / OPEN-QUESTIONS / SECURITY-NOTES / THREAT_MODEL (DRAFT) / VERIFICATION / DEPLOYMENT-LOG | 满足 (content needed fixes) | THREAT_MODEL header not `Status: DRAFT`; source traces in DECISIONS D19, DEPLOYMENT-LOG, OPEN-QUESTIONS | 满足 | THREAT_MODEL.md:3 `Status: DRAFT`; D19 rewritten neutrally; requirement quote removed from OPEN-QUESTIONS; VERIFICATION and DEPLOYMENT-LOG rewritten for this round |

## C. Configuration and deployment

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| C1 | Config as template, input validation, removal policy as a parameter, uniform tags | 需修改 | Real `config/sandbox.json` / `handover.json` committed; prefix `nlctl` | 满足 | `config/*.example.json` tracked, `make init` copies them, real files ignored; `infra/smarthome_infra/config.py:29` validates types, ranges, model id, throttling, tags, prefix rule (synth fails); `removalPolicy` parameter; tags `Project=proto-nl`, `Purpose`, `Teardown` (`infra/app.py`); no account id in `cdk.json` or config (account-agnostic stack, D33) |
| C2 | Preflight (account, Region, tools, quotas), destructive-change guard, smoke/e2e verification | 需修改 | No account confirmation; no `cdk diff` guard; no paid switch; CDK `@latest` | 满足 | `scripts/preflight.py:46` last-4 confirmation (`CONFIRM_ACCOUNT`), API Gateway logging role, paginated profiles, quotas; `scripts/deploy.sh:19` `ALLOW_DESTRUCTIVE=1`; `ALLOW_PAID=1` in `scripts/e2e_test.py`, `scripts/run_eval.py:157`, `local/run_local.py`; CDK CLI pinned 2.1146.0; e2e covers denial, cross-user, async and failure-fallback paths |
| C3 | Verification records with date, Region, configuration, commit, failures, limits | 需修改 | No "what this does not prove"; commit hashes invalid after the history rewrite | 满足 | VERIFICATION.md and DEPLOYMENT-LOG.md: each run has date, Region, config, model, commit hash, failures and a "does not prove" list |

## D. Security and CI

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| D1 | Static checks with zero unhandled findings, every suppression with a reason; trust policies with SourceAccount and SourceArn; resource policies on sensitive resources | 需修改 | nag acknowledgements stack-wide, topic prefix hard-coded, unused L1; runtime SourceArn `...:*`; IoT and flow-log roles without conditions | 满足 | `infra/smarthome_infra/nag.py`: acknowledgements per construct, ids from config; synth of a variant with other prefixes passes; trust policies `stack.py:102` (flow log), `:343` (IoT), `:498` (gateway), `:609` (runtime `runtime/proto_nl_agent*`); Lambda roles documented as not supporting the keys (SECURITY-NOTES.md, with links); runtime resource policy `stack.py:758`; checkov findings classified in VERIFICATION.md |
| D2 | CI: lint, tests, synth, secret scan; Actions pinned by SHA, minimal permissions; Dependabot groups; gitleaks exceptions justified | 需修改 | Synth failed without credentials; no format check, canary, osv, deploy workflow; no Dependabot cooldown | 满足 | `.github/workflows/ci.yml`: lint + format check, tests, mock eval + negative control, credential-free synth (3 configs), gitleaks canary + full history (checksum-pinned binary), osv-scanner; SHAs verified with `git ls-remote`; `permissions: contents: read`; `deploy-verify.yml` (workflow_dispatch, OIDC, `sandbox` environment, placeholder role ARN); `dependabot.yml` weekly, grouped, 7-day cooldown, reasons in comments; `.gitleaks.toml` exceptions with reasons. Not run on GitHub: executed locally only |
| D3 | No account-level settings changed | 满足 | D11 | 满足 | Unchanged; preflight only reads the API Gateway account role; Transaction Search and vended logs left to the account owner (OPEN-QUESTIONS 5, 9) |

## E. Cleanup

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| E1 | Cleanup script with confirmations and a leftover check; README lists what is not removed automatically | 需修改 | `verify_cleanup.py` not paginated, bootstrap assets only a reminder; no cleanup script | 满足 | `scripts/cleanup.sh` (stack-name confirmation, second confirmation for data and bootstrap objects, warn-and-continue); `scripts/verify_cleanup.py` (paginated, more classes, non-zero exit, failed checks count as leftovers); `scripts/bootstrap_assets.py` (asset-hash identification); README "Clean up" list |
| E2 | Every Region used checked; explicit conclusion | 满足 | DEPLOYMENT-LOG (round 2) | 满足 | DEPLOYMENT-LOG.md: us-east-1 and us-west-2 checked after this round's destroy; conclusion stated |

## F. Documents and commits

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| F1 | Fact-check table with official links and dates; cost by dimension only; README section order | 需修改 | Missing rows (direct code deployment, resource policy, Identity scope, scaling, us-west-2, Nova 2 Lite); zh README without diagram and merged final sections; deploy commands in several files | 满足 | docs/architecture.md fact-check rows added (2026-10-21), "observed in testing" marked as such; costs list billing dimensions and pricing pages plus an AWS Budgets note; README and README.zh-CN: same sections in the required order, mermaid diagram in both, deployment commands only in README "Deploy" |
| F2 | Conventional Commits with verification in the body; not pushed | 需修改 | 3 of 17 subjects compliant | 满足 | Subjects rewritten once with git-filter-repo (bodies kept); every 0.1.0 commit is Conventional with what/why/verified; no remote configured |

## AgentCore checklist

| # | Item | Before | Evidence (before) | After | Evidence and handling |
|---|---|---|---|---|---|
| 1 | User id only from verified identity; agent checks payload user = runtime user | 部分满足 | Orchestrator uses Cognito `sub`; agent-side check never ran (no header reaches the container) | 满足 (agent-side check 不适用) | User id from Cognito `sub` (`handler.py` `_route`) or the speaker's registered owner, sent as payload user and `runtimeUserId`. The agent-side comparison is not applicable: the runtime does not pass the user id to the container, so the inert check was removed (D25); only the orchestrator can invoke the runtime (resource policy) |
| 2 | If `runtimeUserId` is used: only one trusted caller has `InvokeAgentRuntimeForUser`; runtime resource policy | 满足 | stack.py resource policy | 满足 | `runtimeUserId` is required for the agent's workload token (found in deployment testing, D25); `InvokeAgentRuntimeForUser` only on the orchestrator role (`stack.py` `invoke_actions`); resource policy allows only that role and denies everyone else; e2e "runtime rejects callers other than the orchestrator" |
| 3 | All service-role trust policies with SourceAccount and SourceArn | 需修改 | Runtime `...:*`; IoT and flow-log roles without conditions | 满足 | See D1; the deployment with the narrowed conditions succeeded and the runtime served the end-to-end checks (DEPLOYMENT-LOG.md) |
| 4 | Identity permissions scoped to the provider; actions covered by the service-linked role removed | 部分满足 | `workload-identity/*` | 满足 | Runtime `workload-identity/proto_nl_agent*`, gateway `workload-identity/proto-nl-gateway*`, token vault and the one provider; runtime has no `GetWorkloadAccessToken*`; its `GetSecretValue` on the one M2M secret was confirmed as required by the deployment check (DEPLOYMENT-LOG.md) |
| 5 | Default-deny Cedar; only needed tools; state-changing tools denied at the gateway | 满足 | stack.py policy engine | 满足 | `stack.py:512`; e2e "gateway denies the device-control tool" |
| 6 | `failedRecords` checked for BatchCreate and BatchDelete | 满足 | memory_store.py | 满足 | `memory_store.py:116` (raise), `:124` (warning) |
| 7 | Eventual consistency: newest wins, retries where needed | 满足 | newest_per_key | 满足 | `memory_store.py:138`; agent read keeps the newest; e2e polls |
| 8 | Namespace format, `indexedKeys`, complete Memory IAM | 满足 | stack.py | 满足 | `/members/<id>/preferences/`; `stack.py:444`; list/create/delete/expiry actions on the one memory |
| 9 | Explicit `lifecycleConfiguration` aligned with the session TTL | 满足 | stack.py | 满足 | `stack.py:697` (`sessionIdleSeconds`, `runtimeMaxLifetimeSeconds`) |
| 10 | Credentials fetched per request | 满足 | main.py | 满足 | `main.py:52` (`_memory`), `:137` (`_gateway_token`) |
| 11 | Runtime always updated on code change; explicit dependency order | 满足 | asset hash as key | 满足 | S3 asset key is the content hash; `stack.py:714-716` dependencies (policy, role, provider, target) |
| 12 | `trace_attributes` with user and session | 满足 | main.py | 满足 | `main.py:189` |
| 13 | Memory and Gateway APPLICATION_LOGS delivery to stack-owned log groups | 不适用 | D19 | 不适用 | Vended log delivery needs an account-level CloudWatch Logs resource policy the stack would not own (D19); listed in the handover hardening checklist |
| 14 | Transaction Search prerequisite documented | 满足 | OPEN-QUESTIONS 5 | 满足 | OPEN-QUESTIONS.md item 5, architecture.md observability |
| 15 | Evaluation set versioned, with expectations and a deliberately wrong control; code-based/online evaluators as follow-up | 部分满足 | No version field | 满足 | `sample-data/eval/cases.json` version 1.1.0 with changelog; `run_eval.py --negative-control` must report failures (CI and `make eval`); online evaluation remains follow-up work (needs Transaction Search) |
| 16 | Every nag suppression with a reason; ASH / gitleaks / detect-secrets run | 部分满足 | Stack-wide suppressions; ASH not run | 满足 | Per-construct acknowledgements with reasons; gitleaks (history + canary), detect-secrets, zizmor, osv-scanner, checkov run locally; ASH attempted, recorded in VERIFICATION.md with the scanners that could not run |
| 17 | Cleanup covers service-created log groups, bootstrap assets, vended-log delivery resources | 部分满足 | Script only reminded about bootstrap assets | 满足 | `cleanup.sh` deletes runtime/provider log groups and workload identities, bootstrap objects by asset hash; no vended-log resources exist (item 13) |
