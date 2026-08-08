## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2024-05-25 - Terminal Feedback Emojis
**Learning:** Terminal outputs for skipped operations (like "No release will be made") often blend in with normal logging output. Adding simple semantic emojis like 💤 (sleeping/skipping) makes it immediately obvious that the process is intentionally doing nothing, rather than failing or hanging.
**Action:** Use contextual emojis (e.g. 💤 for skipped/no-op, 🛑 for errors, :rocket: for success) in plain text outputs where `rich` might be unavailable or for simple messages to improve scannability in CI logs.
