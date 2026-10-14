1. The CI check `check_upstream_ownership.py --run-gate python-lint` failed with `47:5 C901 '_http_status' is too complex (11 > 10)` on `src/semantic_release/bsr/registry.py`.
2. This is because adding the `try/except` block inside `_http_status` increased the cyclomatic complexity.
3. According to the `.jules/sentinel.md` learning and memory, when this happens, we must append a `# noqa: C901` comment directly to the affected function's signature.
4. Modify `src/semantic_release/bsr/registry.py` to add `# noqa: C901` to `def _http_status(`.
5. Run `uv run --no-sync python scripts/check_upstream_ownership.py --check` and `uv run --no-sync python scripts/check_upstream_ownership.py --run-gate python-lint` to verify.
6. Run `ruff format` and tests to ensure everything is still clean.
7. Complete pre-commit.
8. Submit.
