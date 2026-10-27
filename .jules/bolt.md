## 2023-10-27 - Performance optimizations
**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.
