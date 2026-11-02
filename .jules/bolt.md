## 2023-10-27 - Performance optimizations
**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.

## 2024-11-03 - Optimize os.walk by modifying dirs in-place
**Learning:** When traversing directory trees using `os.walk`, checking against path components iteratively with `any(...)` after all paths are yielded can result in unnecessary overhead since `os.walk` descends into unwanted hidden directories (e.g., `.git`, `.venv`).
**Action:** Modifying the yielded `dirs` list in-place (e.g., `dirs[:] = [d for d in dirs if not d.startswith(".")]`) prevents `os.walk` from descending into those directories at all, providing an extreme speedup.
