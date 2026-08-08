## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.
## 2024-05-24 - Do not add new emojis to CLI output
**Learning:** The maintainer has explicitly decided not to add new emojis to the project's output. Existing shortcodes (`:x:` and `:rocket:`) are inherited from upstream `python-semantic-release`, but the fork is not extending them. Additionally, changing the default CLI output can break downstream CI systems that parse the logs. Finally, changes to CLI outputs can conflict with ongoing work to add machine-readable output formats (`--format json`).
**Action:** Do not propose micro-UX improvements that add new emojis or change the existing text of CLI outputs. Focus UX enhancements elsewhere, or ensure they strictly follow the project's constraints (e.g., as written in `AGENTS.md`).
