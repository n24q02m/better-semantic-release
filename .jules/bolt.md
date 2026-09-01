## 2024-05-24 - Avoid `all()` and `getattr()` in performance-critical methods

**Learning:** For performance-critical methods comparing a fixed set of attributes (like `__eq__` used heavily in version sorting), using `all()` with a generator expression and `getattr()` introduces significant function call and generator overhead. Direct attribute access with explicit boolean chaining is ~7.7x faster and eliminates this bottleneck.
**Action:** When implementing dunder methods like `__eq__` or `__hash__` in highly utilized classes, favor direct attribute access and explicit short-circuiting over concise dynamic attribute evaluation.
