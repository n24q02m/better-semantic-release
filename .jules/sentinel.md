## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-05-18 - Prevent SSRF in urllib requests
**Vulnerability:** Calls to `urllib.request.urlopen()` and `urllib.request.Request()` with user-controlled or external URLs without protocol scheme validation can be exploited to read local files via the `file://` scheme or perform Server-Side Request Forgery (SSRF) to internal network addresses.
**Learning:** Even when the Bandit linter ignores `# noqa: S310`, dynamic URLs constructed from registry names and package names require explicit runtime scheme validation to ensure they only connect over HTTP/HTTPS protocols.
**Prevention:** Always parse the URL using `urllib.parse.urlparse()` and assert that `parsed_url.scheme` is strictly in `("http", "https")` before passing it to `urllib.request.urlopen()`.
