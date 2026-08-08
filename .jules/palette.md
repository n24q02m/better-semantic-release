## 2024-05-19 - Semantic error outputs with emojis
**Learning:** Raw `click.echo` prints plain text that users often miss in wall-of-text CI logs. Replacing this with rich `rprint` using red text and visual signs (like 🛑 or ❌) makes fatal CLI states stand out, significantly improving terminal UX accessibility. The `rich` CLI testing runner strips markup, meaning tests aren't typically broken when adding markup text.
**Action:** Always prefer `rprint` with appropriate semantic colors and emojis over plain `click.echo` for CLI error outputs. Use `rich.markup.escape` when printing dynamic strings to prevent `MarkupError`.

## 2024-05-24 - Semantic Error/Warning Colors
**Learning:** Terminal errors and warnings displayed with basic `click.echo` often get lost in noisy CI logs or standard output streams. By using Rich's colors and emojis, developers get instant visual confirmation of the failure state.
**Action:** Always favor formatting terminal CLI errors and warnings with appropriate semantics (e.g. `[bold red]` and `:x:`) over plain text.

## 2024-05-30 - Rejected UX Change: CLI Output Convention
**Learning:** Adding visual delight like emojis to CLI terminal output is generally a good UX pattern, but not when the output serves as a machine-readable contract. Maintainers of this project explicitly rejected adding new emojis because downstream pipelines parse and rely on the exact literal output prefixes (e.g., `NotAReleaseBranch`). Adding arbitrary characters breaks the contract.
**Action:** Before decorating CLI messages, verify if they are part of a stable downstream-visible contract or machine-readable interface. Do not add emojis or rewrite string prefixes that tests or pipelines specifically match against unless it's a completely unparsed human-only message stream.
