## 2024-05-18 - Avoiding O(N²) List Concatenation in Python with `reduce`
**Learning:** Using `reduce` with a lambda like `lambda acc, item: acc + item` on lists creates a new list object on every iteration, leading to O(N²) time complexity for N items. This pattern in `sort_numerically` caused noticeable slowdowns when sorting thousands of tags.
**Action:** Replace `reduce(lambda acc, item: acc + item, lists, [])` with a direct `for` loop using `list.extend()` or `list.append()`, which modifies the list in place and gives O(N) complexity. Always check `reduce` usages in Python for hidden performance traps with mutable collections.

## 2024-07-24 - Avoiding `reduce` with list concatenation for list flattening
**Learning:** Continuing on the previous learning, I found multiple instances in this codebase (`semantic-release`) where `reduce` is used to flatten lists (e.g. `reduce(lambda all_msgs, msgs: all_msgs + msgs, map(...), [])` or `reduce(lambda acc, p_results: [*acc, ...], ...)`). This results in O(N^2) time complexity and memory allocations because it creates a new list copy on every iteration.
**Action:** Replace these usages of `reduce` with O(N) alternatives such as list comprehensions (e.g., `[item for sublist in lists for item in sublist]`) or `itertools.chain.from_iterable` to significantly improve performance when handling larger numbers of commits.

## 2024-08-01 - Rejected speculative optimization on `all()`
**Learning:** A PR to replace `all([a, b, c])` with `a and b and c` to avoid list allocations was rejected. The reason was twofold: (1) The code was not on a measured hot path (it runs alongside Git I/O, which dominates execution time). (2) In this specific repo, stock files from `python-semantic-release` should be kept as close to upstream as possible to minimize rebase conflicts; BSR additions live in `src/semantic_release/bsr/`.
**Action:** Never optimize code purely on speculation if it isn't causing a measured bottleneck, especially when the optimization modifies upstream/stock files and adds permanent rebase maintenance cost. Always measure the hot paths and focus optimizations on those.
