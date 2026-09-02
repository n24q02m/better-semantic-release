## 2024-05-24 - Explicit boolean chaining for __eq__
**Learning:** Using explicit boolean chaining in __eq__ over all() with generators significantly improves performance.
**Action:** Use explicit boolean chaining for performance-critical methods.
## 2024-05-24 - Rejected Optimization: Version.__eq__ explicit short-circuiting
**Learning:** The upstream low-churn policy is strictly enforced. Even if a micro-optimization (like replacing `all()` generators with explicit boolean chaining) provides a measurable speedup (e.g., 85% faster in isolation), it will be rejected as noise if it doesn't solve a proven bottleneck or failing contract in the real-world application architecture.
**Action:** Do not propose structural optimizations to stock upstream files (like `version.py`) unless accompanied by a concrete, application-level benchmark proving a significant, real-world bottleneck.
