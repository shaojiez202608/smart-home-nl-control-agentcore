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

1. Work against the latest source on the default branch.
2. Keep changes focused; if you reformat everything it is hard to review the change itself.
3. Run the checks before opening a pull request:

   ```bash
   uv sync --all-groups
   uv run ruff check .
   uv run pytest
   uv run python scripts/run_eval.py
   npx aws-cdk@latest synth -c config=config/sandbox.json --quiet   # runs cdk-nag
   ```

4. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat(core): ...`, `fix(infra): ...`); the
   body says what changed, why, and how it was verified. One logical change per pull request.
5. Never commit credentials, account IDs (use the documentation placeholder `111122223333`), customer data, or
   generated files under `.build/` or `.demo/`.
6. Every change needs tests. Changes to validation, policy, ambiguity or memory rules also need evaluation cases.
7. If you use AI assistants, you must be able to explain every line they produced (see `AGENTS.md`).

## Security issue notifications

If you discover a potential security issue, follow [SECURITY.md](SECURITY.md) rather than opening a public issue.

## Licensing

See the [LICENSE](LICENSE) file. We will ask you to confirm the licensing of your contribution.
