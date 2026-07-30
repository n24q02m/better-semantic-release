## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2024-06-25 - CLI Error Semantic Formatting with `rprint`
**Learning:** Replaced `click.echo(..., err=True)` with `rprint` from `semantic_release.cli.util` using `[bold red]:x:` semantics to make error states in the CLI output stand out and significantly improve terminal accessibility. The original automated code review feedback to change `rprint` to output to stderr using `rich.console.Console(stderr=True)` is safely disregarded since `rprint` in `semantic_release.cli.util` already inherently outputs to `sys.stderr`.
**Action:** Always favor formatting terminal CLI errors with appropriate visual semantics over plain text and always verify and update `e2e` tests which may assert on exact string matching including emojis!
