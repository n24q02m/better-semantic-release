## 2024-05-23 - Python `__eq__` Performance with `all()` and generator vs Explicit Chaining
**Learning:** Using `all(getattr(self, attr) == getattr(other, attr) for attr in (...))` in frequently called dunder methods like `__eq__` introduces substantial overhead due to generator initialization, function call overhead for `getattr`, and the iteration process. For `Version.__eq__`, which is heavily relied upon in sorting and comparing version objects (e.g., semantic sorting of git tags), this abstraction can slow down comparisons by ~10x compared to an explicit boolean chain.
**Action:** Replace `all()` with a generator expression in performance-critical `__eq__` methods with explicit short-circuiting attribute comparisons (`self.major == other.major and self.minor == ...`).

## 2024-05-23 - Python Eager Evaluation with `all()` and `any()`
**Learning:** Using `all([...])` or `any((...))` with explicitly defined lists or tuples forces eager evaluation of all elements in the collection, completely negating the short-circuiting benefits of these functions. Additionally, the list/tuple creation overhead slows down execution significantly.
**Action:** Replace `all([...])` and `any((...))` with explicit boolean chaining (e.g., `a and b and c` or `a or b or c`) to restore short-circuiting behavior and eliminate sequence allocation overhead, yielding measurable performance improvements.
