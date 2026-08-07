## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-08-07 - Runtime URL Validation to Prevent SSRF/LFI
**Vulnerability:** `urllib.request.urlopen` calls were made without explicit URL scheme validation, creating a potential Server-Side Request Forgery (SSRF) or Local File Inclusion (LFI) risk via the `file://` scheme.
**Learning:** Relying solely on `# noqa: S310` to bypass Bandit's static analysis warnings ignores the underlying risk. Even if static analysis tools flag an AST, runtime validation of the URL scheme ensures safety.
**Prevention:** Always enforce runtime URL scheme validation (e.g., explicitly checking for `http` or `https` using `urllib.parse.urlparse`) before passing URLs to `urlopen`, rather than just using a suppression comment.
