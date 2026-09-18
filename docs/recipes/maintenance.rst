.. _bsr-recipe-maintenance:

Fork maintenance guide
======================

BSR is a long-lived fork of python-semantic-release. The boundaries below are
enforced by ``config/bsr-upstream-ownership.toml`` and checked in CI
(``check_upstream_ownership`` + gate-coverage tests).

Ownership zones
---------------

- **Upstream-owned** (copy/sync on upstream pulls): the parser, changelog
  pipeline, commit conventions, ``[tool.semantic_release]`` config handling.
- **BSR-owned** (never overwritten by syncs): everything under
  ``src/semantic_release/bsr/`` and its tests.

Review rules when syncing upstream
----------------------------------

1. Re-apply upstream changes only to upstream-owned paths.
2. Every changed file must have an ``[[ownership]]`` entry *in the same
   commit* that changes it -- CI fails on unowned changes.
3. New modules must declare their gates
   (``python-lint``/``python-format``/``python-type``) so coverage counts stay
   honest (``eligible == covered``).
4. Divergence in an upstream file needs an explicit conflict review: prefer
   extracting BSR behavior into a ``bsr/`` module over editing upstream files.
