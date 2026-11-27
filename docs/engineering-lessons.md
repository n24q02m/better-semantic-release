# Engineering lessons

These entries were recovered from the working ledgers this repository kept at its root
until 2026-11-11: the notes of a discontinued automated assistant that proposed changes
under two remits -- performance (`bolt.md`) and security (`sentinel.md`). The entries
are kept verbatim, each in the shape it was written. The `**Commit:**` line under a
heading names the commit that landed that entry, so a settled question can be found in
the repository history before the same change is proposed again. A few entries quote the
paths the ledgers occupied before this move; those paths no longer exist.

Entries under *Upstream adoption* are written here directly. Each one records an
upstream python-semantic-release change that was evaluated for this fork, what was
decided, and the evidence behind the decision, so the evaluation is not repeated at the
next upstream sync.

# Performance (`bolt.md`)

## 2023-10-27 - Performance optimizations

**Commit:** `6a2fc4e`

**Learning:** Found several places where generator initialization overhead could be avoided.
**Action:** Expand `any` with generator expressions into explicit boolean chains or short-circuiting loops where applicable.

# Security (`sentinel.md`)

## 2026-11-01 - SSRF bypass through malformed IPv6 hostnames

**Commit:** `924615d`

**Vulnerability:** An SSRF protection logic in `_http_status` allowed connecting to malformed internal/loopback IPv6 addresses (e.g. `http://::1:8080`) because `urllib.parse.urlparse` sets `.hostname` to `None` for these unbracketed IPv6 addresses, bypassing the validation check that only ran if `host` was truthy.
**Learning:** `urllib.request.urlopen` can still successfully process certain malformed URLs where `urllib.parse.urlparse` fails to extract the hostname.
**Prevention:** Always fail securely by explicitly rejecting requests when `urllib.parse.urlparse(url).hostname` returns `None`.

# Upstream adoption

## 2026-11-27 - Typed-variable version stamping (upstream v10.7.0) deferred to the upstream sync

**Upstream change:** python-semantic-release v10.7.0, "cmd-stamp: expand version stamp
mechanism to support typed python variables" (PR #1485, commit `947c57b`). It widens the
regular expression built by `PatternVersionDeclaration.from_string_definition` with an
optional annotation group, `(?:\s{0,8}:\s{0,8}[^\s=:][^=:=]{0,100})?`, between the
variable name and the operator, so a `version_variables` entry such as
`src/pkg/__init__.py:__version__` also stamps `__version__: str = "1.0.0"`.

**Decision:** not adopted now. The change arrives with the upstream sync that brings in
v10.7.0, and it needs a fix before it lands there.

**Evidence:** the stock pattern and the v10.7.0 pattern were run side by side against the
same inputs, stamping `1.1.0`:

- `__version__: str = "1.0.0"` -- stock: no match; v10.7.0: matches (the intended gain).
- `version: 1.0.0` followed by the line `other = "2.0.0"`, with the variable `version`
  -- stock rewrites the first line to `version: 1.1.0`; v10.7.0 leaves it untouched and
  rewrites the second line to `other = "1.1.0"`. The annotation class `[^=:=]` admits
  newlines, so the match runs past the end of the line and the version lands in an
  unrelated assignment, with no error.

**Reasons:**

- It is a change to a stock file this fork has not diverged
  (`src/semantic_release/version/declarations/pattern.py`). Carrying it now adds a stock
  delta with no reason that could not wait for the sync, against the rule that fork
  logic lives under `src/semantic_release/bsr/`.
- As released, it can silently stamp the wrong line in colon-style files (YAML-like
  `key: value` declarations, which upstream documents as supported) whenever an `=`
  assignment holding a version follows within 100 characters. A silent wrong write is
  worse than the current miss.
- Typed variables already have a route: a `text` version target with an explicit
  `{version}` pattern stamps them, and it fails closed when the pattern does not match.
- The current miss is not silent either: `doctor` reports a declaration whose version
  cannot be read under `VERSION_TARGETS`.

**Revisit:** at the upstream sync that brings in v10.7.0. Add a regression test for the
cross-line case above, and exclude newlines from the annotation class (for example
`[^=:\n]`) before the sync lands.
