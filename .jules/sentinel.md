## 2026-09-07 - [action.sh SSH Key Handling]
**Vulnerability:** The GitHub Action shell script writes the SSH public key and allowed signers using `echo` and redirect operators, but the `.ssh` directory and `allowed_signers` file are created without explicit strict permissions.
**Learning:** TOCTOU risks and lack of explicit permissions on `.ssh` creation expose keys to concurrent processes or untrusted local processes within CI runners.
**Prevention:** Apply `chmod 700 ~/.ssh` after creation, and utilize `printf` instead of `echo` to prevent unintended processing of flags by different shell environments.

## 2026-09-07 - [action.sh Private Key Hash Leak]
**Vulnerability:** Outputting the SHA256 hash of the SSH private key (`sha256sum ~/.ssh/signing_key`) in a shell script leaks credential derivatives and fingerprinting metadata to standard output.
**Learning:** Even if the plaintext key is protected (e.g. avoiding `cat`), emitting cryptographic hashes of secrets into CI/CD build logs exposes fingerprinting data that could be leveraged by attackers.
**Prevention:** Never compute and print hashes of secrets or sensitive files in CI/CD pipeline scripts.

## 2024-10-15 - Prevent SSH key fingerprint leaks in CI logs
**Vulnerability:** The `ssh-add` command prints the added key's fingerprint and metadata to standard output, which gets captured and exposed in CI/CD build logs.
**Learning:** Tools that handle secrets often emit metadata (like hashes or fingerprints) to standard output by default, creating credential derivatives that leak sensitive environment properties.
**Prevention:** Always redirect standard output of secret-handling commands (like `ssh-add`) to `/dev/null` to ensure build logs remain clean of cryptographic fingerprints.
