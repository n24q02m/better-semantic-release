## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2024-07-29 - Semantic CLI Errors Refactor
**Learning:** Adding emojis and colors (e.g. `[bold red]:x:`) to CLI error outputs in `src/semantic_release/cli/cli_context.py` using `rich.print` (via `rprint`) successfully enhances terminal accessibility by making errors stand out more clearly compared to `click.echo`. Tests also required adjustment to accommodate these new emoji outputs.
**Action:** Consistently replace `click.echo` with `rprint` to emit colorful and semantic error messages, but always verify test output behavior, especially when end-to-end tests assert exact stderr/stdout output.
