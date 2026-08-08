## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2025-02-22 - Rejected UX change: CLI output decorators and Emojis
**Learning:** Cosmetic changes to CLI output strings (adding emojis or modifying text) can break downstream systems that parse these logs, and these changes often clash with larger refactoring efforts like machine-readable (JSON) outputs. Furthermore, adding new emojis is strictly rejected by the maintainer in this repository to prevent unstated output bloat.
**Action:** Do NOT add new emojis or modify default CLI output formatting/wording unless it solves a critical bug. These are considered disruptive downstream changes rather than micro-UX improvements. Always clean up temporary artifacts like `patch.diff` or `patch.py` before submission.
