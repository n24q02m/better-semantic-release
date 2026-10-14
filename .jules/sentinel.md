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

## 2026-09-08 - [SSRF bypass via alternative IP representations]
**Vulnerability:** The HTTP probe SSRF protections rely on string matching hostnames, which can be bypassed via decimal/octal IP representations (e.g., `http://2130706433/` for `127.0.0.1`).
**Learning:** String matching hostnames for SSRF protection is insufficient as standard urllib libraries will correctly interpret and resolve decimal, octal, and hexadecimal IP formats.
**Prevention:** Use `socket.getaddrinfo` (not `socket.gethostbyname` which breaks IPv6) to resolve IPs and validate with the `ipaddress` module.
