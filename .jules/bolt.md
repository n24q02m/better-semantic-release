## 2023-10-27 - Performance optimizations
**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.

## 2026-10-31 - [Optimization: Stop `os.walk` descending into hidden directories]
**Learning:** [When traversing directory trees using `os.walk`, checking against hidden directories later via `any(...)` generator expression causes `os.walk` to unnecessarily traverse and process the contents of all hidden directories (e.g. `.git`, `.venv`). This introduces significant I/O and processing overhead. Modifying the yielded `dirs` list in-place (e.g. `dirs[:] = [d for d in dirs if not d.startswith(".")]`) prevents `os.walk` from descending into those directories, which is dramatically faster.]
**Action:** [When using `os.walk` and needing to skip directories based on some criteria, use the ability to modify the `dirs` list in-place to prune the traversal instead of filtering the resulting paths post-traversal.]
