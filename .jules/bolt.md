## 2024-05-18 - Avoiding O(N²) List Concatenation in Python with `reduce`
**Learning:** Using `reduce` with a lambda like `lambda acc, item: acc + item` on lists creates a new list object on every iteration, leading to O(N²) time complexity for N items. This pattern in `sort_numerically` caused noticeable slowdowns when sorting thousands of tags.
**Action:** Replace `reduce(lambda acc, item: acc + item, lists, [])` with a direct `for` loop using `list.extend()` or `list.append()`, which modifies the list in place and gives O(N) complexity. Always check `reduce` usages in Python for hidden performance traps with mutable collections.

## 2024-07-24 - Avoiding `reduce` with list concatenation for list flattening
**Learning:** Continuing on the previous learning, I found multiple instances in this codebase (`semantic-release`) where `reduce` is used to flatten lists (e.g. `reduce(lambda all_msgs, msgs: all_msgs + msgs, map(...), [])` or `reduce(lambda acc, p_results: [*acc, ...], ...)`). This results in O(N^2) time complexity and memory allocations because it creates a new list copy on every iteration.
**Action:** Replace these usages of `reduce` with O(N) alternatives such as list comprehensions (e.g., `[item for sublist in lists for item in sublist]`) or `itertools.chain.from_iterable` to significantly improve performance when handling larger numbers of commits.

## 2024-07-30 - Avoiding `all()` and `any()` with explicit lists
**Learning:** Using `all([cond1, cond2])` or `any([cond1, cond2])` forces eager evaluation of all conditions inside the list and allocates memory for the list, completely defeating the purpose of short-circuit evaluation. This is significantly slower than using explicit boolean operators like `cond1 and cond2` or `cond1 or cond2`, which evaluate lazily.
**Action:** Replace `all([...])` and `any([...])` with explicit boolean operators (e.g. `and`, `or`) when the number of conditions is fixed, or use generator expressions instead of list comprehensions if iterating over a sequence (e.g., `all(cond for cond in seq)` instead of `all([cond for cond in seq])`).
