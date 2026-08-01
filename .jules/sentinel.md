## 2023-11-20 - Command Injection in GitHub Actions Output
**Vulnerability:** GitHub Actions environment variables (like `$GITHUB_OUTPUT`) populated with multiline values using a hardcoded `EOF` delimiter are vulnerable to injection attacks if the content contains the string `\nEOF\n`.
**Learning:** This codebase incorrectly implemented GitHub Actions multiline strings using a static `EOF` string. When creating variables out of user-controlled input (like commit messages appearing in `release_notes`), a malicious commit message could break out of the multiline variable and define new environment variables or outputs.
**Prevention:** Always use a randomly generated delimiter (e.g. `uuid.uuid4().hex`) to ensure there is no possibility of collisions or command injection when writing to `$GITHUB_OUTPUT`.

## 2024-05-24 - SSRF and Local File Inclusion via urlopen
**Vulnerability:** `urllib.request.urlopen` will process non-HTTP schemas like `file://` by default unless explicitly restricted. When used with user-influenced URLs, this allows Local File Inclusion (reading arbitrary local files) and Server-Side Request Forgery.
**Learning:** Static analyzers like Bandit check for `# noqa: S310` to bypass this, but silencing the linter isn't a fix. The `urlopen` call must have runtime URL scheme validation (`http`, `https`) before making the request.
**Prevention:** Always enforce runtime URL scheme validation (using `urllib.parse.urlparse`) before making requests with `urllib.request.urlopen`.
