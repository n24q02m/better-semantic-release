## 2024-06-25 - Python's functools.reduce for list concatenation is a hidden O(N^2) operation
**Learning:** In Python, using `reduce(lambda acc, next_item: acc + next_item, ...)` to concatenate lists requires copying the entire accumulated list on every iteration. For large lists, this turns an otherwise O(N) operation into an O(N^2) memory and CPU bottleneck.
**Action:** When flattening or concatenating lists, always use `.extend()` in a standard loop or a list comprehension (`[item for sublist in lists for item in sublist]`) to avoid this hidden cost and keep complexity at O(N).
