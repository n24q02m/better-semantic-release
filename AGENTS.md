# Agent Collaboration

## Quick reference

- Repo: `n24q02m/better-semantic-release`
- Description: Drop-in python-semantic-release fork with built-in release-safety guards (orphan-tag + registry-collision).
- License: Apache-2.0
- Docs toolchain: reStructuredText. `README.rst`, `CHANGELOG.rst` and `CONTRIBUTING.rst` are the real files; do not add Markdown twins.

## Build & Test

See `README.rst` for end-user install. For development:

```sh
mise run setup     # First-time dev environment
mise run lint      # Ruff + mypy over the fork's additions and patched files
mise run test      # Full default suite
mise run test-bsr  # Only tests/unit/semantic_release/bsr — fast inner loop
mise run fix       # Auto-fix lint + format in the fork's own modules
```

The task bodies mirror `.github/workflows/ci.yml`. Ruff runs with `select = ["ALL"]`,
which stock python-semantic-release does not satisfy, so both linters are scoped to
`src/semantic_release/bsr/` plus the stock files this fork patches.

## Where this fork's code lives

Additions go in `src/semantic_release/bsr/` and are wired into stock files behind a
`# BSR-PATCH:` comment marking the call site. Stock files stay as close to upstream as
possible so rebases remain cheap. A change to stock code needs a reason that could not
be satisfied inside `bsr/`.

## Machine-readable output

`version` and `publish` accept `--format json`. Prefer it over parsing the human output.

Under that flag, **stdout carries exactly one JSON document and nothing else**, on every
exit either command has — a run that releases, a run that does not, `--print`,
`--print-last-released`, and the failure paths. So `json.loads` over the whole stream is
safe without special-casing; there is no "except when…" to remember. Narration and logs
already go to stderr, including at `-vv`.

Without the flag, stdout is byte-for-byte what it was before the option existed. That
parity is a tested guarantee, not an intention — `tests/unit/semantic_release/bsr/
test_jsonout_cli.py` asserts the exact bytes. Any change to a stdout line in
`cli/commands/` breaks it on purpose.

`schema_version` is `1` and is shared by both documents. Adding a field is fine; removing
one or changing what it means is a `schema_version` bump. Both documents are assembled in
`bsr/jsonout.py` rather than inline at the call sites, so keep them there — that is what
stops the two surfaces drifting into two dialects. Field reference:
`docs/api/commands.rst`, under the `--format` option of each command.

## Release

Releases are triggered manually via `workflow_dispatch` on `cd.yml`. Choose `beta` or
`stable`. better-semantic-release performs the version bump, changelog, tag and GitHub
Release. It is a drop-in fork, so configuration keys are unchanged
(`[semantic_release]` / `[tool.semantic_release]`).

## Conventions

- Commits: only `feat:` and `fix:` prefixes, enforced by the pre-commit `commit-msg` hook.
- Test coverage: aim for >= 95% on `src/semantic_release/bsr/`, the code this fork owns.
- No secrets in code or commit history.
- CLI output is a downstream contract. Twenty-one repositories read this tool's output
  from their CI logs, and the `--print` / `--print-tag` exits emit one datum on stdout
  that scripts parse. Do not add emojis, icons or prose to existing output lines. When
  a state is genuinely hard to consume, add a field to the machine-readable document
  behind `--format json` and cover it with a test. The `:x:` and `:rocket:` shortcodes
  still present in stock files are inherited from upstream and stay as upstream wrote them.
- A performance change needs a measurement in the pull request body: the command, the
  before and after, and the input size.

The per-bot ledgers under `.jules/` record which proposals were rejected and why. Read
the entry for your area before opening a pull request.
