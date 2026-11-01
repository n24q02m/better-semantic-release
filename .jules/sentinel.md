## 2026-11-01 - SSRF bypass through malformed IPv6 hostnames
**Vulnerability:** An SSRF protection logic in `_http_status` allowed connecting to malformed internal/loopback IPv6 addresses (e.g. `http://::1:8080`) because `urllib.parse.urlparse` sets `.hostname` to `None` for these unbracketed IPv6 addresses, bypassing the validation check that only ran if `host` was truthy.
**Learning:** `urllib.request.urlopen` can still successfully process certain malformed URLs where `urllib.parse.urlparse` fails to extract the hostname.
**Prevention:** Always fail securely by explicitly rejecting requests when `urllib.parse.urlparse(url).hostname` returns `None`.
