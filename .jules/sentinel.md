## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-05-18 - SSRF and Local File Inclusion via `urllib.request.urlopen`
**Vulnerability:** `urllib.request.urlopen` can be exploited to read local files if a `file://` URL is provided, or make requests to internal services (SSRF) if no URL validation is performed. While Bandit (S310) flags these, developers sometimes just add a `# noqa: S310` comment.
**Learning:** We must perform runtime URL scheme validation for 'http' or 'https' using `urllib.parse.urlparse` before passing any potentially unsafe URL to `urllib.request.urlopen`.
**Prevention:** Always enforce strict URL scheme validation (allowlisting 'http' and 'https') prior to making HTTP requests with standard libraries like `urllib`.
