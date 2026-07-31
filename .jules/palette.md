## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.
## 2024-08-01 - Rich Markup Tag Best Practices
**Learning:** Adding explicit closing tags (e.g., `[/green]`) to `rich` statements where the styling applies to the very end of the string isn't strictly necessary since `rich` handles unclosed tags gracefully, but it is a good practice for maintainability. Also, scratchpad files like `plan.md` shouldn't be left in the root directory.
**Action:** Close rich markup explicitly for robustness. Ensure temporary files are cleaned up prior to requesting code review or submitting.
