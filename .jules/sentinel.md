## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2025-02-27 - SSRF/LFI Risk in urllib.request.urlopen
**Vulnerability:** The `_http_status` function used `urllib.request.urlopen` to probe registry URLs but lacked runtime validation of the URL scheme, which allowed arbitrary protocols like `file://` to be loaded. This exposed an SSRF/LFI vulnerability if user-controlled input or manipulated dependencies ever constructed unexpected URLs.
**Learning:** Even if a static analyzer like Bandit triggers an alert (`B310`), silencing it with `# noqa: S310` is insufficient and dangerous without real runtime validation. The `urlopen` utility inherently supports dangerous schemes by default, requiring explicitly enforced allow-lists (e.g., `http` and `https`) prior to request execution.
**Prevention:** Always parse the URL (e.g., `urllib.parse.urlparse`) and assert that its `scheme` is strictly within an approved set (`http`, `https`) before passing it to `urlopen` or similar HTTP request utilities.
