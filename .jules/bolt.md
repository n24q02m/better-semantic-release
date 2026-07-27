## 2024-05-18 - Avoiding O(N²) List Concatenation in Python with `reduce`
**Learning:** Using `reduce` with a lambda like `lambda acc, item: acc + item` on lists creates a new list object on every iteration, leading to O(N²) time complexity for N items. This pattern in `sort_numerically` caused noticeable slowdowns when sorting thousands of tags.
**Action:** Replace `reduce(lambda acc, item: acc + item, lists, [])` with a direct `for` loop using `list.extend()` or `list.append()`, which modifies the list in place and gives O(N) complexity. Always check `reduce` usages in Python for hidden performance traps with mutable collections.

## 2024-07-24 - Avoiding `reduce` with list concatenation for list flattening
**Learning:** Continuing on the previous learning, I found multiple instances in this codebase (`semantic-release`) where `reduce` is used to flatten lists (e.g. `reduce(lambda all_msgs, msgs: all_msgs + msgs, map(...), [])` or `reduce(lambda acc, p_results: [*acc, ...], ...)`). This results in O(N^2) time complexity and memory allocations because it creates a new list copy on every iteration.
**Action:** Replace these usages of `reduce` with O(N) alternatives such as list comprehensions (e.g., `[item for sublist in lists for item in sublist]`) or `itertools.chain.from_iterable` to significantly improve performance when handling larger numbers of commits.

## 2024-05-18 - Avoiding Repeated Regex Compilation
**Learning:** Initializing lists containing raw regex strings and dynamically calling `re.compile()` inside frequently executed functions introduces compilation and memory overhead, even with `re` module internal caching.
**Action:** Extract reusable regex patterns to module-level constants and pre-compile them using `re.compile()` once during module initialization.
