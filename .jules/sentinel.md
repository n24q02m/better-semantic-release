## 2023-10-27 - Git URL Token Masking in Logs
**Vulnerability:** GitCommandError exceptions logged with `logger.exception` bypass credential masking.
**Learning:** `logger.exception` automatically appends standard tracebacks which print raw exception values (bypassing custom string-level masking).
**Prevention:** Switch to `logger.error` combined with explicit credential masking for git command errors.
