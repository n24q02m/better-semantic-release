## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-05-18 - SSRF/LFI via urllib.request
**Vulnerability:** Unvalidated URLs passed to `urllib.request.urlopen` can allow Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) by using schemes like `file://` or `ftp://` instead of `http/https`.
**Learning:** Even though `urllib` was meant for a REST API call, without runtime scheme enforcement an attacker controlling the `url` parameter can fetch local files or query internal network services. Static analysis tools like Bandit (B310) flag this correctly but are sometimes ignored with `# noqa`.
**Prevention:** Always parse the URL using `urllib.parse.urlparse` and validate that `scheme in ("http", "https")` before passing the URL to `urllib.request.urlopen`.
