.. _bsr-recipe-gated:

Gated release recipe
====================

For repos where a release must wait on human approval before it reaches
users, BSR's ``gated`` mode splits the pipeline at the gate: everything up
to and including verification runs unattended, then the pipeline pauses on
a GitHub Environment until a configured reviewer approves, and only then
is the release published. A denial or a deadline expiry aborts the gate —
nothing is published, and re-running the pipeline never publishes twice.

The state machine behind the recipe lives in
``semantic_release.bsr.gating``; the workflow file below is the executable
form of the same transitions (asserted by
``tests/e2e/test_gated_release_e2e.py``, which pins the environment name
to ``DEFAULT_GATE_ENVIRONMENT``).

Workflow
--------

Copy ``tests/e2e/gated/recipe.workflow.yml`` from the BSR repository into
your repo's ``.github/workflows/`` (it is act-compatible and needs no
secrets beyond the default token):

.. code-block:: yaml

   jobs:
     release:
       environment: release-gate   # the gate: pauses until a reviewer approves
       steps:
         - uses: actions/checkout@v4
         # ... plan + verify steps ...
         - name: publish
           if: env.GATE_DECISION == 'approved'
           run: bsr publish --version "${{ inputs.version }}"

Configure reviewers on the environment (repo *Settings → Environments →
release-gate → Required reviewers*) and, optionally, a wait timeout.
GitHub pauses the job before any publish step executes; approval resolves
the pause, rejection or timeout aborts it.

Configuring BSR
---------------

.. code-block:: toml

   [tool.semantic_release]
   mode = "gated"

   [tool.semantic_release.gated]
   environment = "release-gate"   # default; must match the workflow job

Semantics
---------

- **prepare** — the ungated portion of the pipeline builds and verifies
  the release candidate without publishing.
- **await-approval** — the Environment gate holds the run; the adapter
  surfaces the reviewer's decision.
- **publish** — only an *approved* decision reaches publication.
- **deny / timeout** — the gate is aborted; nothing is published, ever.
- **retry** — re-running a published gate is idempotent: terminal gates
  are sticky and the publish step is never executed twice.

For local rehearsal without a cloud round-trip, drive the same flow in
Python with ``CallbackApprovalAdapter`` — see the module docstring of
``semantic_release.bsr.gating`` and the e2e test for the exact call
sequence.
