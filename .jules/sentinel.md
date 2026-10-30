## 2026-10-24 - SSRF zone-identifier bypass
**Vulnerability:** A Server-Side Request Forgery (SSRF) bypass exists in `_is_blocked_host` via IPv6 zone identifiers (e.g., `127.0.0.1%en0`, `[::1%en0]`). `urllib.parse.urlparse` allows and forwards zone identifiers to `urllib.request.urlopen`, but `ipaddress.ip_address` fails to parse them, resulting in the blocklist logic treating them as safe external hostnames.
**Learning:** `urllib.request` can resolve IP literals obfuscated with zone IDs, which bypasses `ipaddress.ip_address` parsing and basic string match blocklists.
**Prevention:** Explicitly strip surrounding IPv6 brackets (`[` and `]`) and network interface zone identifiers (`%` and anything following it) before parsing the host with `ipaddress` for blocklist validation.
