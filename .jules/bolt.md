## 2024-05-15 - [Initial]
**Learning:** Setup bolt journal
**Action:** Ready to record performance learnings

## 2024-05-15 - [all() generator overhead in Python]
**Learning:** For performance-critical methods comparing a fixed set of attributes (like `__eq__` used heavily in sorting), avoid using `all()` with a generator expression and `getattr()`. The function call and generator overhead makes it significantly slower than direct attribute access.
**Action:** Use direct attribute access with explicit boolean chaining (`self.attr == other.attr and ...`) to eliminate generator overhead and achieve significantly faster execution.
