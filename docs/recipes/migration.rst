.. _bsr-recipe-migration:

Migrating from upstream python-semantic-release
===============================================

What stays the same
-------------------

- The ``[tool.semantic_release]`` table, branch configuration, commit
  conventions and changelog pipeline are upstream-compatible.
- Existing PSR configs keep working: absent a ``[tool.semantic_release.bsr]``
  table, BSR behaves like upstream with defaults.

What BSR adds
-------------

- ``[tool.semantic_release.bsr]``: schema-versioned BSR block (unknown fields
  fail closed).
- Version sources/targets, publishers, component graph/path map (monorepo
  planning), notes sources, hooks, and the publish-readiness probe.
- ``plan`` / ``verify`` CLI commands for a read-only control loop before any
  mutation.

Adopting without workflow surgery
---------------------------------

1. Add a minimal ``[tool.semantic_release.bsr]`` with ``schema_version = 1``.
2. Run ``semantic-release plan`` in CI (read-only) and inspect the summary.
3. Add ``verify`` gates (guards, ownership manifest, path filter) at your own
   pace.
4. Only then move publishing under ``[tool.semantic_release.bsr.publish]``.
