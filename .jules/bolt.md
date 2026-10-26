## 2026-10-26 - Eliminate generator initialization overhead in string validation loops
**Learning:** `any()` with a generator expression introduces significant initialization overhead in string validation paths (e.g. `any(ord(char) < 0x20 for char in url)`).
**Action:** Replace `any()` with C-level pre-compiled regex objects (e.g., `CONTROL_CHAR_RE.search(url) is not None`) to drastically reduce overhead while preserving functionality and readability.

## 2026-10-26 - Eliminate generator initialization overhead in path validation
**Learning:** `any()` with a generator expression introduces significant initialization overhead in path validation checking (e.g. `any(part in {"", ".", ".."} for part in parts)`).
**Action:** Replace `any()` with direct explicit boolean `in` checks against the sequence (e.g., `"" in parts or "." in parts or ".." in parts`) to drastically reduce overhead.
