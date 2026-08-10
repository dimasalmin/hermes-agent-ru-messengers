# Contributing

Pull requests and focused bug reports are welcome.

Before opening a pull request:

1. Keep changes inside the external plugin boundary; do not patch Hermes core.
2. Do not include real API tokens, cookies, production databases, logs, or
   private messages.
3. Add or update deterministic contract tests for behavior changes.
4. Run:

   ```bash
   python -m compileall -q plugins tests scripts
   python -m pytest -q
   git diff --check
   ```

5. Describe separately what was verified locally and what still needs a live
   MAX/VK acceptance test.

Live tests must use disposable credentials and an explicitly allowlisted test
user/community. Never use an open-access override for a public deployment.
