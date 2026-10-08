## 2026-09-07 - [action.sh SSH Key Handling]
**Vulnerability:** The GitHub Action shell script writes the SSH public key and allowed signers using `echo` and redirect operators, but the `.ssh` directory and `allowed_signers` file are created without explicit strict permissions.
**Learning:** TOCTOU risks and lack of explicit permissions on `.ssh` creation expose keys to concurrent processes or untrusted local processes within CI runners.
**Prevention:** Apply `chmod 700 ~/.ssh` after creation, and utilize `printf` instead of `echo` to prevent unintended processing of flags by different shell environments.

## 2026-09-07 - [action.sh Private Key Hash Leak]
**Vulnerability:** Outputting the SHA256 hash of the SSH private key (`sha256sum ~/.ssh/signing_key`) in a shell script leaks credential derivatives and fingerprinting metadata to standard output.
**Learning:** Even if the plaintext key is protected (e.g. avoiding `cat`), emitting cryptographic hashes of secrets into CI/CD build logs exposes fingerprinting data that could be leveraged by attackers.
**Prevention:** Never compute and print hashes of secrets or sensitive files in CI/CD pipeline scripts.

## 2026-09-07 - [action.sh SSH Metadata Leak]
**Vulnerability:** Native secret-handling commands like `ssh-agent` and `ssh-add` emit metadata (such as agent PIDs and key fingerprints) to standard output by default, leaking them into CI/CD build logs.
**Learning:** When evaluating `ssh-agent`, the output must be redirected outside the subshell substitution (e.g. `eval "$(ssh-agent -s)" > /dev/null`) because redirecting inside discards required export commands before they can be evaluated.
**Prevention:** Always redirect output of `ssh-agent` and `ssh-add` to `/dev/null` in CI/CD scripts to prevent leaking credential derivatives.

## 2026-09-08 - [SSRF Protection in HTTP Probes]
**Vulnerability:** HTTP probes in the registry allow fetching arbitrary URLs without restricting private or local addresses, presenting a Server-Side Request Forgery (SSRF) risk.
**Learning:** Probe adapters querying user-provided URLs can be abused to access internal metadata services (e.g. 169.254.169.254) or local endpoints.
**Prevention:** Implement host validation in base HTTP request functions (like `_http_status`) to fail closed on internal/loopback hostnames.
## 2025-02-27 - Fix SSRF via explicit IP loopback checks
**Vulnerability:** The registry probe HTTP client used string matching against "localhost", "127.0.0.1", "0.0.0.0", and "169.254.169.254" to block SSRF attempts. This is easily bypassed by utilizing octal IPs (0177.0.0.1), short IPs (127.1), or custom DNS records pointing to internal services.
**Learning:** Checking hostnames directly against strings is a common source of SSRF bypasses because network APIs parse variants of the same destination (short IPs or IPv6 translations).
**Prevention:** Always use `socket.getaddrinfo` to resolve hostnames before requests and validate the returned addresses with the `ipaddress` module's explicit property flags like `is_loopback` and `is_link_local` to filter internal connections correctly.
