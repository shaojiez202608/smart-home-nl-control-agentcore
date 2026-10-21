# Contributing Guidelines

Thank you for your interest in contributing. Whether it's a bug report, new feature, correction, or additional
documentation, we value feedback and contributions from the community.

Please read through this document before submitting any issues or pull requests.

## Reporting bugs / feature requests

Use the GitHub issue tracker. Check existing open and recently closed issues first. Please include:

* A reproducible test case or series of steps
* The version of the code being used
* Any modifications you've made relevant to the bug
* Anything unusual about your environment or deployment

## Contributing via pull requests

1. Work on a topic branch from the latest default branch; pull requests are squash-merged.
2. Keep changes focused; if you reformat everything it is hard to review the change itself.
3. Run the checks before opening a pull request (none of them needs AWS credentials):

   ```bash
   make init
   make lint test eval synth scan-ci   # ruff, tests, mock evaluation + negative control, cdk-nag, secret scans
   ```

   Deployment checks (`make deploy`, `ALLOW_PAID=1 make verify`, `make destroy`, `make verify-cleanup`) are for
   maintainers with a disposable sandbox account; see the README.
4. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat(core): ...`, `fix(infra): ...`); the
   body says what changed, why, and how it was verified. One logical change per pull request.
5. Never commit credentials, account IDs (use the documentation placeholder `111122223333`), real or customer
   data, real parameter files (`config/*.json`; only `*.example.json` templates are tracked), or generated files
   under `.build/`, `.demo/`, `.deploy/` or `cdk.out/`.
6. Every change needs tests. Changes to validation, policy, ambiguity or memory rules also need evaluation cases;
   never relax an expectation to make a run pass.
7. Changes to CI, scanning configuration, AI-assistant configuration or cdk-nag acknowledgements need the code
   owner's review (`.github/CODEOWNERS`).
8. If you use AI assistants, you must be able to explain every line they produced (see `AGENTS.md`).

## Security issue notifications

If you discover a potential security issue, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

## Licensing

See the [LICENSE](LICENSE) file. We will ask you to confirm the licensing of your contribution: by submitting a
pull request you confirm that you have the right to contribute it under the MIT-0 license.
