## 2024-10-27 - Server-Side Request Forgery via Zone ID in IP Literals
**Vulnerability:** The registry HTTP probe functionality allowed loopback/metadata SSRF by appending a zone identifier (e.g., `%en0`) to IP literals, which bypassed the `_is_blocked_host` regex/string parsing protections while still being routable in `urllib.request`.
**Learning:** Checking hostnames directly from parsed URLs requires removing potential IPv6 brackets (`[` and `]`) and network interface zone identifiers (`%<interface>`) which can obfuscate blocked IP literals like `127.0.0.1%en0`.
**Prevention:** Before parsing a host via `ipaddress.ip_address` or regex matching in SSRF blocklists, always explicitly remove surrounding brackets (`[]`) and strip off zone IDs using `.split("%")[0]`.
