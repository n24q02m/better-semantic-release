better-semantic-release
***********************

*A drop-in fork of python-semantic-release with built-in release-safety guards.*

----

``better-semantic-release`` is a drop-in fork of `python-semantic-release`_ (MIT licensed).
It keeps the same ``[tool.semantic_release]`` configuration schema, the same
``semantic-release`` CLI, and the same GitHub Action interface -- switching over only
requires pointing the ``uses:`` line at the fork:

.. code-block:: yaml

    uses: n24q02m/better-semantic-release@v<major>

On top of that, the fork adds release-safety guards that run automatically before any
commit, tag, or push is made. Guards can be opted out of per-repository under the
``[tool.semantic_release.bsr]`` table in ``pyproject.toml``.

How it differs from upstream
=============================

.. list-table::
   :header-rows: 1
   :widths: 40 25 35

   * - Behavior
     - Upstream (python-semantic-release)
     - better-semantic-release
   * - Orphaned release-tag detection (a rebase or force-push silently
       freezes releases on a tag nobody notices)
     - None
     - Built-in, fails loud
   * - Registry-collision detection (re-publishing a version that already
       exists on the target registry)
     - None
     - Built-in, fails closed
   * - Monorepo commit path filtering (commits outside a component's
       configured path(s) count toward its version bump / changelog)
     - None
     - Opt-in, off by default (drop-in)
   * - Config / CLI / GitHub Action interface
     - --
     - Identical (drop-in)

.. note::

   The guards are **on by default**. The registry-collision guard auto-targets PyPI for a
   project that declares ``[project].name`` and **fails closed** -- if the registry is
   unreachable (network / rate-limit / 5xx) it aborts the release rather than risk a double
   publish, which couples release availability to the registry's uptime. Tune or disable per
   repository under ``[tool.semantic_release.bsr]`` (``guard_orphan_tag``,
   ``guard_registry_collision``, ``registry = "pypi" | "npm" | "none"``).

.. note::

   Guards evaluate even under ``--noop`` / dry-run -- this is deliberate, so a dry-run
   surfaces would-be blocks instead of silently skipping the safety checks a real release
   would hit. In dry-run, the registry-collision guard still performs a live, read-only
   HTTP GET against the target registry (PyPI/npm) to check whether the computed version
   already exists; no commit, tag, or push is made either way.

Monorepo path filtering
-----------------------

Upstream's ``directory:`` input only selects *where* config (``pyproject.toml``,
``tag_format``, ...) is read from -- it does not filter *which* commits are analyzed, so
in a monorepo a commit touching an unrelated component still bumps this component's
version and shows up in its changelog. This is opt-in and **off by default** (drop-in):
enable it under ``[tool.semantic_release.bsr]``.

.. code-block:: toml

    [tool.semantic_release.bsr]
    path_filter = true
    paths = ["apps/api", "libs/shared"]

- ``path_filter`` (default ``false``) -- master switch. When ``false`` the fork behaves
  identically to upstream: every commit since the last release counts, regardless of
  which paths it touched.
- ``paths`` (default ``[]``, repository-root-relative) -- one or more path prefixes; a
  commit only counts toward this component if it changed a file under one of them.
  Multiple entries are OR'd together, which supports a component that also depends on
  shared code (e.g. ``["apps/api", "libs/shared"]``).
- When ``path_filter`` is ``true`` and ``paths`` is left empty, it defaults to the run
  directory (the GitHub Action's ``directory:`` input) made relative to the repository
  root.

.. note::

   Path filtering applies to **both** the version-bump computation and the generated
   changelog -- a commit excluded from one is excluded from the other.

----

Python Semantic Release
***********************

*Automating Releases via SemVer and Commit Message Conventions*

----

The official documentation for Python Semantic Release can be found at
`python-semantic-release.readthedocs.io`_.

GitHub Action
=============

When using the Python Semantic Release GitHub Action, it executes the command
``semantic-release version`` using `python-semantic-release`_.

The usage information and examples for this GitHub Action is available under
the `GitHub Actions section`_ of `python-semantic-release.readthedocs.io`_.

.. _python-semantic-release: https://pypi.org/project/python-semantic-release/
.. _python-semantic-release.readthedocs.io: https://python-semantic-release.readthedocs.io/en/stable/
.. _GitHub Actions section: https://python-semantic-release.readthedocs.io/en/stable/configuration/automatic-releases/github-actions.html
