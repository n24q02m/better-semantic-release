## 2023-10-27 - Performance optimizations
**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.

## 2024-11-08 - Use explicit 'in' check instead of any() with generator for small lists
**Learning:** Checking for a specific element (like an empty string `""`) in a list using `any(not part for part in parts[3:])` introduces generator initialization overhead and is significantly slower than using an explicit `in` check (`"" in parts[3:]`).
**Action:** When searching for empty strings or specific literal elements in a slice, use the `in` operator instead of `any()` with a generator expression.
