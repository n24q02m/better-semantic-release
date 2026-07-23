## 2024-07-23 - Python list concatenation using `reduce` creates O(N^2) complexity

**Learning:** When flattening a list of lists in Python, using `reduce(lambda acc, next_item: acc + next_item, lists, [])` is an anti-pattern. Because lists are mutable but the `+` operator on lists creates an entirely new list rather than modifying in-place, this operation has an O(N²) time complexity and creates quadratic memory allocations. For larger inputs, this becomes a severe bottleneck.

**Action:** Replace `reduce(lambda a, b: a + b, lists, [])` with list comprehensions for flattening `[item for sublist in lists for item in sublist]` or `itertools.chain.from_iterable(lists)`. Both of these alternatives provide O(N) complexity since they avoid recreating intermediate lists.
