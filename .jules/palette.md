## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## YYYY-MM-DD - [CLI Semantic Colors]
**Learning:** For CLI output using `rich`, standard colors like `green` imply success. Using `green` for a "skipped" or "warning" message can be confusing. Using `yellow` with a `:warning:` emoji provides better semantic meaning and a more intuitive user experience. Emojis can add a nice touch of visual polish to CLI feedback.
**Action:** Always ensure that CLI message colors semantically match the message intent (e.g., green for success, yellow for warning/skip, red for error). Use `rich` emoji shortcodes (like `:white_check_mark:` or `:warning:`) to add visual cues. Avoid leaving temporary test scripts in the repository.
