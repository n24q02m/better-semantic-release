## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.
## 2024-05-18 - Added HTTP scheme verification
**Vulnerability:** URL scheme not restricted to HTTP(s) for `urlopen`.
**Learning:** Even if URLs are currently constructed safely, defensive coding requires validating scheme before dynamic requests to prevent `file://` or SSRF in the future.
**Prevention:** Always parse and validate URL schemes before calling HTTP clients.
