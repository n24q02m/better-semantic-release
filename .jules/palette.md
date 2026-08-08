## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2024-07-30 - Rejected UX Change: Emojis in CLI Output
**Learning:** Adding new emojis and changing prefix wordings to the CLI output using `rich.print` (such as `[bold red]:x:`) in `better-semantic-release` was rejected. Maintainers confirmed three constraints: 1) No new emojis will be introduced; the existing ones are inherited upstream legacy. 2) Altering default CLI outputs breaks downstream CI log parsers. 3) Output formatting is currently being overhauled for machine-readable JSON formats so aesthetic churn creates conflicts.
**Action:** Do not propose micro-UX enhancements that add emojis, change text prefixes, or modify the CLI output text styling in this repository as it breaks downstream consumers and conflicts with larger architectural priorities.
