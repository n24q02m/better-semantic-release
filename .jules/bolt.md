## 2026-08-27 - Avoid all([...]) in tight loops
**Learning:** Using all([...]) bypasses boolean short-circuiting and allocates list memory on each iteration, causing performance penalties in tight loops.
**Action:** Use explicit boolean short-circuiting (and) instead.
