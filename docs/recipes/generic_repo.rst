.. _bsr-recipe-generic:

Releasing a generic repository
============================

Any repo with a version-bearing file and a GitHub Release can use BSR with no
registry at all: bump the file, attach artifacts through a hook, publish as a
GitHub Release (or a plain shell step).

.. code-block:: toml

    [tool.semantic_release]
    tag_format = "v{version}"

    [tool.semantic_release.bsr]
    schema_version = 1

    [[tool.semantic_release.bsr.version.sources]]
    kind = "git-tag"

    [[tool.semantic_release.bsr.version.targets]]
    kind = "text"
    path = "VERSION"
    pattern = "{version}"

    [[tool.semantic_release.bsr.publish.publishers]]
    kind = "github-release"
    manifest_path = "dist/manifest.json"
    workspace = ".bsr"

    [[tool.semantic_release.bsr.hooks]]
    point = "pre_publish"
    command = ["make", "dist"]

Or publish through an arbitrary shell command:

.. code-block:: toml

    [tool.semantic_release.bsr]
    schema_version = 1

    [[tool.semantic_release.bsr.publish.publishers]]
    kind = "shell"
    name = "upload"
    command = ["./scripts/upload-release.sh"]

Notes:

- A ``github-release`` publisher needs a probe manifest produced by your
  build; the ``shell`` publisher's exit code decides publication success --
  fail closed by convention.
- The readiness probe treats ``github-release`` as trivially ready; probe
  ``kind`` can be set under ``[tool.semantic_release.bsr.publish.probe]``.
