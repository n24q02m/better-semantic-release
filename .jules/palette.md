## 2024-05-19 - Semantic error outputs (superseded, see 2026-08-08)
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Routing fatal CLI states through rich `rprint` with a colour makes them stand out.
**Action:** Superseded by the 2026-08-08 entry below. Read that entry before proposing any change to CLI output.

## 2024-05-24 - Semantic Error/Warning Colors (superseded, see 2026-08-08)
**Learning:** Terminal errors and warnings displayed with basic `click.echo` can get lost in noisy CI logs.
**Action:** Superseded by the 2026-08-08 entry below. Read that entry before proposing any change to CLI output.

## 2026-08-08 - CLI output is a downstream contract; improvements go into the JSON document
**Learning:** The two entries above each carried a standing "always add emojis" instruction, and between 2026-07-25 and 2026-08-07 they produced thirteen open pull requests proposing the same decoration of the same lines (#37, #38, #40, #42, #44, #48, #52, #56, #59, #64, #65, #70, #73). All thirteen were closed. Three facts make that class of change wrong for this repository:

1. The colour-and-icon pass on the fatal paths is already complete, landed at commit `a7f10a1`. The `:x:` and `:rocket:` shortcodes still present elsewhere are inherited from upstream python-semantic-release and are deliberately left as upstream wrote them.
2. This tool runs in the CI of twenty-one repositories that read its output from their logs. Changing the wording or prefix of a line is a downstream-visible contract change, so it needs a stated consumer, not an aesthetic argument. #59 showed the shape of the problem: it rewrote two e2e assertions so the `NotAReleaseBranch` message required a literal warning-sign prefix.
3. The remaining `click.echo` calls in `cli/commands/version.py` at the `--print` and `--print-tag` exits are data, not narration. Scripts parse them. They stay exactly one datum on stdout, unstyled.

**Action:** When you find a CLI state that is genuinely hard to consume, add or extend a field in the machine-readable document produced by `--format json` (`src/semantic_release/bsr/jsonout.py`) and cover it with a test, rather than adding a new prose line or icon to stdout or stderr. If a change to an existing human-facing string is unavoidable, name the consumer it is for in the pull request body and show the before and after of the affected e2e assertion.
