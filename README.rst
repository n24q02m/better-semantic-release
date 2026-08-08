better-semantic-release
***********************

*A drop-in fork of python-semantic-release with built-in release-safety guards.*

.. image:: https://github.com/n24q02m/better-semantic-release/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/n24q02m/better-semantic-release/actions/workflows/ci.yml
   :alt: CI status

.. image:: https://github.com/n24q02m/better-semantic-release/actions/workflows/cd.yml/badge.svg
   :target: https://github.com/n24q02m/better-semantic-release/actions/workflows/cd.yml
   :alt: CD status

.. image:: https://codecov.io/github/n24q02m/better-semantic-release/graph/badge.svg
   :target: https://app.codecov.io/github/n24q02m/better-semantic-release
   :alt: Coverage of src/semantic_release/bsr

.. image:: https://img.shields.io/pypi/v/better-semantic-release
   :target: https://pypi.org/project/better-semantic-release/
   :alt: Latest release on PyPI

.. image:: https://img.shields.io/pypi/pyversions/better-semantic-release
   :target: https://pypi.org/project/better-semantic-release/
   :alt: Supported Python versions

.. image:: https://img.shields.io/badge/license-Apache--2.0-blue
   :target: https://github.com/n24q02m/better-semantic-release/blob/main/LICENSE
   :alt: Apache-2.0 licence

----

``better-semantic-release`` is a drop-in fork of `python-semantic-release`_ (MIT licensed).
This fork is distributed under Apache-2.0; the upstream MIT terms are preserved in ``LICENSE-MIT``.
It keeps the same ``[tool.semantic_release]`` configuration schema, the same
``semantic-release`` CLI, and the same GitHub Action interface -- switching over only
requires pointing the ``uses:`` line at the fork:

.. code-block:: yaml

    uses: n24q02m/better-semantic-release@v<major>

.. note::

   For anything beyond a quick trial, pin the full commit SHA and record the exact
   version beside it, as `GitHub's own hardening guide`_ recommends for third-party
   actions:

   .. code-block:: yaml

       uses: n24q02m/better-semantic-release@<40-char-sha>  # v1.2.3

   The trailing comment is not decoration -- it is what Renovate (and Dependabot)
   read to decide what the pin currently *is*. Pin the SHA but write ``# v<major>``
   in the comment and the bot tracks a **moving** tag: the only update it can offer
   is an opaque "update digest" pull request with no version anywhere in it, which
   is how a pin silently falls several minor versions behind while every dashboard
   still looks healthy.

On top of that, the fork adds release-safety guards that run automatically before any
commit, tag, or push is made. Guards can be opted out of per-repository under the
``[tool.semantic_release.bsr]`` table in ``pyproject.toml``.

.. contents:: Table of contents
   :depth: 2
   :local:

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
   * - Why a run did (or did not) release -- the real reason, not the
       misattributed "already released" message
     - Only at ``INFO`` log level, never surfaced
     - Opt-in, off by default (``explain``)
   * - Recurring cryptic failures (bad config key, unknown commit parser,
       missing git remote, prerelease-bump mismatch)
     - Raw ``str(exc)``, or an uncaught traceback
     - Opt-in "what / why / fix" messages (``actionable_errors``)
   * - Per-component "what would this release do" plan for a monorepo
     - None
     - Opt-in report-only table (``summary``)
   * - Stable release notes after a prerelease line (prerelease tags
       "consume" the commits, leaving the stable section empty)
     - Won't fix upstream
     - Opt-in aggregation (``stable_notes_aggregate``)
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

Configuration reference
-----------------------

Every fork addition lives under ``[tool.semantic_release.bsr]`` in the same config file
upstream already reads (``pyproject.toml``, ``setup.cfg``, ``releaserc.toml``, ...). The
two guards are **on** by default; everything else is **off** by default, so an untouched
config behaves exactly like upstream.

.. list-table::
   :header-rows: 1
   :widths: 32 18 50

   * - Key
     - Default
     - What it does
   * - ``guard_orphan_tag``
     - ``true``
     - Fail loud when the version recomputes to an already-released tag that is no
       longer reachable from ``HEAD`` (a rebase / force-push dropped the release commit).
   * - ``guard_registry_collision``
     - ``true``
     - Fail closed when the computed version already exists on the target registry.
   * - ``registry``
     - auto
     - ``"pypi"``, ``"npm"`` or ``"none"``. Auto-targets PyPI when ``[project].name``
       is declared.
   * - ``path_filter`` / ``paths``
     - ``false`` / ``[]``
     - Count only commits touching the configured path prefixes toward this
       component's bump and changelog.
   * - ``explain``
     - ``false``
     - Report the real reason a run did or did not release.
   * - ``actionable_errors``
     - ``false``
     - Replace recurring cryptic failures with "what / why / fix" messages.
   * - ``summary`` / ``components``
     - ``false`` / ``[]``
     - Print a report-only, per-component release plan for a monorepo.
   * - ``stable_notes_aggregate`` / ``stable_notes_scope``
     - ``false`` / ``"line"``
     - Fold the notes of intervening prereleases into the stable release they
       finalize.

Every diagnostic below writes to **stderr**, so ``semantic-release version`` keeps
printing only the version number on stdout and stays safe to capture in a shell
substitution.

Release diagnostics (``explain``)
---------------------------------

When ``next_version()`` recomputes a version that already exists, upstream always prints
the same line -- ``No release will be made, X has already been released!`` -- no matter
what actually happened. The usual real cause is that **no commit since the last release
qualified for a bump**, a fact upstream only logs at ``INFO`` and never surfaces.

.. code-block:: toml

    [tool.semantic_release.bsr]
    explain = true

With ``explain`` on, that line is replaced by the classified cause:

- ``NO_QUALIFYING_COMMITS`` -- commits were scanned, none were releasable (no
  ``feat`` / ``fix`` / breaking-change commits). This is the case upstream misattributes.
- ``ALREADY_RELEASED_NOOP`` -- the current tip is already tagged; a genuine re-dispatch
  with nothing new to release.
- ``ORPHAN`` -- the version recomputes to an already-released but *unreachable* tag,
  which is the silent-release-freeze the orphan-tag guard exists for.

On a run that *does* release, it also prints a "why this bump" line -- the bump level,
the per-commit-type breakdown behind it, the base version, and how many commits were
scanned::

    better-semantic-release explain: minor bump from 2 feat, 3 fix commit(s) since 1.4.0 (5 commit(s) scanned).

Actionable error messages (``actionable_errors``)
-------------------------------------------------

Upstream surfaces its most-cited failures as a bare ``str(exc)`` with no framing, and
leaves two of them uncaught entirely (raw traceback).

.. code-block:: toml

    [tool.semantic_release.bsr]
    actionable_errors = true

With the flag on, these categories are rewritten as "what happened / why / how to fix":

- **PRERELEASE BUMP MISMATCH** -- a prerelease-level bump was requested but the base
  version is not already a prerelease.
- **INVALID CONFIGURATION** -- each failing key is listed as
  ``[tool.semantic_release.<key>]: <reason>`` instead of a raw pydantic dump.
- **PARSER LOAD FAILED** -- lists the valid parser names (``angular``, ``conventional``,
  ``conventional-monorepo``, ``emoji``, ``scipy``) and the ``module:ClassName`` form.
- **GIT REMOTE NOT FOUND** -- shows both fixes (add the remote, or point
  ``[tool.semantic_release.remote]`` at an existing one).
- **TAG_FORMAT MISMATCH** -- a note emitted when the repository has git tags but *none*
  match the configured ``tag_format``, which otherwise makes upstream silently treat the
  repository as having no prior releases and start over from the initial version.

.. note::

   ``MissingGitRemote`` and ``ParserLoadError`` are not caught by upstream at all. With
   ``actionable_errors`` off they are deliberately re-raised unchanged, so output stays
   byte-for-byte identical to upstream; only with the flag on are they caught and
   enriched. This is pure message enrichment -- no new exception types, and no change to
   exit codes.

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

Monorepo release-plan summary (``summary``)
-------------------------------------------

Neither upstream's ``conventional-monorepo`` parser nor the path filter above tells you
what a release run *would* do across a monorepo's components before it does it.

.. code-block:: toml

    [tool.semantic_release.bsr]
    summary = true
    components = [
        { name = "api", paths = ["apps/api", "libs/shared"] },
        { name = "web", paths = ["apps/web"] },
    ]

Each row is computed with the **same** ``next_version()`` upstream uses for the real
release, scoped to that component's paths:

.. code-block:: text

    better-semantic-release summary: monorepo release plan
    component  would-release  level       commits  sample paths                            version
    ---------  -------------  ----------  -------  --------------------------------------  -------
    api        yes            MINOR       4        apps/api/main.go, apps/api/handlers.go  1.4.0
    web        no             NO_RELEASE  0        -                                       1.2.3

- The report is **read-only** -- nothing is committed, tagged, pushed or persisted, and
  it renders before any persistence step, so it appears whether or not the run itself
  ends up releasing.
- ``components`` is optional. Left empty, you get a single row built from ``bsr.paths``
  and named after ``[project].name`` -- so a non-monorepo project still gets a plan
  instead of an empty report.
- A component with no ``paths`` means "the whole repository" (the filter is a
  passthrough), not "nothing".

Stable release notes aggregation (``stable_notes_aggregate``)
-------------------------------------------------------------

Upstream buckets every commit under the *nearest* tag walking back from ``HEAD``. Once a
line has ``rc``/``beta`` tags, those prereleases have already "consumed" the commits, so
finalizing the stable version with no brand-new commits of its own produces a release
section that is empty -- or fragmented across the prerelease tags instead of one grouped
``vX.Y.Z`` section. This is the most-cited changelog complaint upstream (issues #555,
#817, #1377, #1440) and is won't-fix there.

.. code-block:: toml

    [tool.semantic_release.bsr]
    stable_notes_aggregate = true
    stable_notes_scope = "line"  # or "since_stable"

With this on, the notes of every intervening prerelease are merged into the stable
release that finalizes them, de-duplicated by commit sha, covering **both** the written
changelog and the VCS release notes. It only ever runs for a genuine stable finalize; a
prerelease run is untouched.

- ``scope = "line"`` (default) -- fold in prereleases sharing the stable version's
  ``major.minor.patch``.
- ``scope = "since_stable"`` -- walk back from the new version and fold in *every*
  intervening prerelease regardless of line, stopping at (and excluding) the previous
  stable tag. This differs from ``"line"`` when a prerelease track was abandoned
  mid-line: if a forced bump moved ``0.2.0-beta.1`` to ``1.0.0-beta.1``, ``"line"``
  picks up only ``1.0.0-beta.1`` while ``"since_stable"`` also folds in the abandoned
  ``0.2.0-beta.1``.

.. note::

   This is off by default because -- unlike the diagnostics above -- it changes the
   changelog **content**, which is a committed artifact.

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

.. _GitHub's own hardening guide: https://docs.github.com/en/actions/reference/secure-use-reference#using-third-party-actions
.. _python-semantic-release: https://pypi.org/project/python-semantic-release/
.. _python-semantic-release.readthedocs.io: https://python-semantic-release.readthedocs.io/en/stable/
.. _GitHub Actions section: https://python-semantic-release.readthedocs.io/en/stable/configuration/automatic-releases/github-actions.html
