.. _plan-schema:

Release Plan Document (Schema v1)
=================================

The ``plan`` command projects the release decision into a single, stable,
machine-readable document. It is the contract between ``better-semantic-release``
and any consumer that wants to inspect what a release *would* do — CI steps,
dashboards, custom automation — without parsing human output.

.. code-block:: console

   semantic-release --noop plan --format json          # one document on stdout
   semantic-release --noop plan --format markdown --write release-plan.md

The JSON document is rendered from :class:`semantic_release.bsr.plan.ReleasePlan`
by ``to_document()``. Golden tests lock the exact bytes of every render path
(JSON, markdown, table, ``--write`` artifact), so the documented shape below
cannot drift from the code silently.

Top-level fields
----------------

=========================== ============================ =========================================================================
Field                       Type                         Meaning
=========================== ============================ =========================================================================
``schema_version``          ``int``                      Document schema version; ``1`` today. See `Versioning`_ below.
``released``                ``bool``                     Whether this run would cut a release.
``version``                 ``str | null``               The version this run would produce; ``null`` when no release is planned.
``tag``                     ``str | null``               The VCS tag ``version`` would produce (config-defined prefix); ``null`` when no release is planned.
``is_prerelease``           ``bool``                     ``version`` carries a pre-release segment (``-`` before any ``+`` metadata).
``previous_version``        ``str | null``               The most recent released version; ``null`` on an unreleased repository.
``head_sha``                ``str | null``               HEAD commit the plan was computed from; ``null`` only on a repository with no commits. Consumed by the ``verify --plan`` / ``publish --plan`` drift checks.
``decision``                ``object | null``            Why no release happens (see below). ``null`` when ``released`` is ``true`` or on the forced-level path.
``bump``                    ``object | null``            Aggregated commit-scan statistics (see below).
``components``              ``array``                    Per-component rows for a monorepo component map; empty when not configured.
``blockers``                ``array``                    Guard trips that would stop the release; empty when the plan is clean.
``registry``                ``object | null``            Read-only registry probe outcome; ``null`` by default (the plan is offline unless ``--check-registry`` is passed).
``publish_target``          ``object | null``            Where the release would be published; ``null`` unless publish configuration is resolvable.
=========================== ============================ =========================================================================

``decision`` — no-release reason
--------------------------------

Present when the run would *not* release and the cause was classified:

=================== =========================================================================
Field               Meaning
=================== =========================================================================
``code``            One of ``NO_QUALIFYING_COMMITS`` (commits exist, none qualify for a bump), ``ALREADY_RELEASED_NOOP`` (zero commits since the last release), ``ORPHAN`` (computed version is not reachable from ``HEAD`` — history rewritten?).
``commit_count``    Raw count of commits since the last release (pre-qualification).
=================== =========================================================================

``bump`` — commit-scan statistics
---------------------------------

===================== =========================================================================
Field                 Meaning
===================== =========================================================================
``level_bump``        One of ``NO_RELEASE`` | ``PRERELEASE_REVISION`` | ``PATCH`` | ``MINOR`` | ``MAJOR``; ``null`` when unknown (forced-level path).
``commit_count``      Commits behind the level decision.
``type_counts``       Mapping of commit type (``feat``, ``fix``, …) to count.
===================== =========================================================================

``components[]`` — monorepo rows
--------------------------------

======================= =========================================================================
Field                   Meaning
======================= =========================================================================
``name``                Component name from the ``[tool.semantic_release.bsr]`` component map.
``would_release``       Whether this component would be released.
``level``               ``LevelBump`` name for the component: ``NO_RELEASE`` | ``PRERELEASE_REVISION`` | ``PATCH`` | ``MINOR`` | ``MAJOR``.
``commit_count``        Qualifying commits for the component.
``sample_paths``        Up to a few changed paths attributed to the component.
``resulting_version``   The version the component would land at.
======================= =========================================================================

``blockers[]`` — guard trips
----------------------------

=================== =========================================================================
Field               Meaning
=================== =========================================================================
``code``            Machine-readable guard identifier (e.g. ``ORPHAN_TAG``). The set of codes is **open**: new guards may add new codes at any time within schema v1.
``message``         Human-readable description of the trip.
``remediation``     Suggested operator action.
=================== =========================================================================

``registry`` / ``publish_target``
---------------------------------

``registry``: ``{ "registry": str, "reachable": bool | null, "detail": str | null }`` —
``reachable`` is ``null`` when the probe could not determine reachability (e.g.
the offline ``SKIP`` path records no status at all and the whole object stays
``null``).

``publish_target``: ``{ "kind": str, "detail": str | null }`` — ``kind`` is the
publisher adapter identifier (e.g. ``github-release``, ``shell``, ``noop``).

Stability promise (schema v1)
-----------------------------

Within ``schema_version = 1``:

1. **No removals, renames, or type changes.** Every field listed above keeps
   its name, location, and JSON type.
2. **Additions are allowed.** New optional fields or new array entries may
   appear at any time. Consumers MUST ignore unknown fields and unknown
   ``blockers[].code`` values — treating an unknown blocker code as *not*
   blocking is wrong: a plan with a blocker array entry you do not recognize
   should be treated as blocked (fail-closed).
3. **Nullability is part of the contract.** Fields documented as
   ``| null`` above are ``null`` in exactly the documented situations, never
   dropped from the object.
4. The document is deterministic for a fixed repository state when the
   registry probe is not requested: same input, same bytes.

Versioning
----------

- ``schema_version`` stays ``1`` for every backward-compatible addition.
- A **breaking** change — removing or renaming a field, changing a type or a
  field's meaning, tightening nullability — bumps ``schema_version`` to the
  next integer and is announced in the release notes. The new version is a new
  document, not a mutation of this one.
- Readers MUST reject (fail closed) documents whose ``schema_version`` is
  greater than the highest version they implement. Readers may accept lower
  versions only if they implement them.
