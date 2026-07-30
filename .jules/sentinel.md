## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-05-24 - SSRF and Local File Inclusion in urllib.request.urlopen
**Vulnerability:** `urllib.request.urlopen` natively supports URL schemes beyond HTTP and HTTPS, such as `file://` or `ftp://`. If user-controlled input can reach a `urlopen` call, this allows Server-Side Request Forgery (SSRF) and Local File Inclusion (LFI).
**Learning:** Even though static analysis tools (like Bandit `B310`) can be ignored via `# noqa: S310`, relying entirely on bypassing static checks without runtime mitigation leaves the application vulnerable if input isn't fully constrained.
**Prevention:** Always parse the URL (e.g., using `urllib.parse.urlparse`) and explicitly enforce valid protocol schemes (`http`, `https`) *at runtime* before passing the URL to `urllib.request.urlopen`.
