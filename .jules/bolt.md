## 2024-05-18 - Avoiding O(N²) List Concatenation in Python with `reduce`
**Learning:** Using `reduce` with a lambda like `lambda acc, item: acc + item` on lists creates a new list object on every iteration, leading to O(N²) time complexity for N items. This pattern in `sort_numerically` caused noticeable slowdowns when sorting thousands of tags.
**Action:** Replace `reduce(lambda acc, item: acc + item, lists, [])` with a direct `for` loop using `list.extend()` or `list.append()`, which modifies the list in place and gives O(N) complexity. Always check `reduce` usages in Python for hidden performance traps with mutable collections.

## 2024-07-24 - Avoiding `reduce` with list concatenation for list flattening
**Learning:** Continuing on the previous learning, I found multiple instances in this codebase (`semantic-release`) where `reduce` is used to flatten lists (e.g. `reduce(lambda all_msgs, msgs: all_msgs + msgs, map(...), [])` or `reduce(lambda acc, p_results: [*acc, ...], ...)`). This results in O(N^2) time complexity and memory allocations because it creates a new list copy on every iteration.
**Action:** Replace these usages of `reduce` with O(N) alternatives such as list comprehensions (e.g., `[item for sublist in lists for item in sublist]`) or `itertools.chain.from_iterable` to significantly improve performance when handling larger numbers of commits.

## 2026-08-08 - A performance claim needs a measurement and a hot path
**Learning:** The two entries above are about a real asymptotic trap: `reduce` with list concatenation is genuinely O(N²). Generalising them to "any eager construct is worth rewriting" produced twelve pull requests between 2026-07-27 and 2026-08-07 (#39, #41, #46, #47, #51, #54, #58, #61, #62, #66, #69, #72) that were all closed. What they proposed was constant-factor, not asymptotic:

- `all([a, b, c])` to `a and b and c` in the prerelease filter at `version/algorithm.py` saves one three-element list allocation per historic tag, next to git tag resolution that costs orders of magnitude more.
- The same rewrite of `not any((...))` in `changelog/release_history.py` saves one allocation per commit, next to parsing that commit.
- Pre-compiling the git-URL normalizer regexes in `helpers.py` (#39) targets `parse_git_url`, which is decorated `@lru_cache(maxsize=512)` and resolves one remote URL per run - and `re.compile` is memoised by the `re` module regardless.

None of the twelve carried a timing. Several also re-wrapped unrelated lines, including the `BSR-PATCH` call sites, which is churn the pull request did not claim to make.

**Action:** Before proposing a performance change, measure it and put the numbers in the pull request body: the benchmark or timing command, the before and after, and the input size used. Restrict proposals to code that a measurement shows is hot, and confine the diff to the lines the measurement covers. Prefer changes inside `src/semantic_release/bsr/`, which this fork owns; stock python-semantic-release files are kept close to upstream so rebases stay cheap, so a change there needs a correspondingly larger measured gain.
## 2024-08-22 - Pushing prefix loops to C speed with `str.startswith(tuple)`
**Learning:** Checking a string against multiple prefixes using a generator expression like `any(path.startswith(f"{prefix}/") for prefix in prefixes)` forces Python to evaluate the loop natively, creating overhead for each iteration. `str.startswith()` actually accepts a tuple of strings directly, offloading the iteration to the C-level implementation. In a hot path like monorepo file matching (`path_filter.py`), this speeds up commit matching by nearly 3x.
**Action:** Replace `any(s.startswith(p) for p in prefixes)` with `s.startswith(tuple_of_prefixes)`. Ensure you pre-compute the tuple of prefixes outside of the loop so you don't rebuild it on every call.
