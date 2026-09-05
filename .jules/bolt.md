## 2024-09-05 - Direct Attribute Access Over all() for Performance
**Learning:** Using `all()` with a generator expression and `getattr()` introduces significant function call and generator overhead. In performance-critical methods comparing fixed attributes, like `__eq__` used heavily in sorting, explicit boolean chaining (e.g., `self.attr == other.attr and ...`) executes up to 10x faster.
**Action:** Always prefer explicit boolean chaining over `all()` and `getattr()` for fixed attribute comparisons in performance-critical code paths.
