## 2024-10-27 - all() with getattr() generator overhead
**Learning:** In performance-critical methods comparing a fixed set of attributes (like `__eq__` used heavily in sorting), avoid using `all()` with a generator expression and `getattr()`. Direct attribute access with explicit boolean chaining (e.g., `self.attr == other.attr and ...`) eliminates function and generator overhead, resulting in significantly faster execution.
**Action:** Replace `all()` generator expressions with explicit boolean short-circuiting for fixed attribute comparisons in performance-critical paths.
