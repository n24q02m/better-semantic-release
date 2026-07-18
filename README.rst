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
