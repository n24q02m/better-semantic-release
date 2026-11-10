# Engineering lessons

These entries were recovered from the working ledgers this repository kept at its root
until 2026-11-11: the notes of a discontinued automated assistant that proposed changes
under two remits -- performance (`bolt.md`) and security (`sentinel.md`). The entries
are kept verbatim, each in the shape it was written. The `**Commit:**` line under a
heading names the commit that landed that entry, so a settled question can be found in
the repository history before the same change is proposed again. A few entries quote the
paths the ledgers occupied before this move; those paths no longer exist.

# Performance (`bolt.md`)

## 2023-10-27 - Performance optimizations

**Commit:** `6a2fc4e`

**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.

# Security (`sentinel.md`)

## 2026-11-01 - SSRF bypass through malformed IPv6 hostnames

**Commit:** `924615d`

**Vulnerability:** An SSRF protection logic in `_http_status` allowed connecting to malformed internal/loopback IPv6 addresses (e.g. `http://::1:8080`) because `urllib.parse.urlparse` sets `.hostname` to `None` for these unbracketed IPv6 addresses, bypassing the validation check that only ran if `host` was truthy.
**Learning:** `urllib.request.urlopen` can still successfully process certain malformed URLs where `urllib.parse.urlparse` fails to extract the hostname.
**Prevention:** Always fail securely by explicitly rejecting requests when `urllib.parse.urlparse(url).hostname` returns `None`.
