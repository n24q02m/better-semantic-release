## 2024-05-18 - Avoid all() and getattr() in critical comparison methods
**Learning:** Using `all()` with a generator expression and `getattr()` introduces unnecessary overhead in performance-critical methods like `__eq__`, resulting in slower execution times. Direct attribute access with explicit boolean chaining (e.g., `self.attr == other.attr and ...`) runs at C-level speed and is up to 10x faster.
**Action:** Avoid `all()` with generator expressions for fixed attribute comparisons. Always use explicit boolean chaining for simple equality checks in `__eq__` methods.
