.. _bsr-recipe-node:

Releasing a Node package
========================

BSR bumps ``package.json`` through a version target, drives the build through
a hook, and publishes through npm with a provenance-capable readiness posture.

.. code-block:: toml

    # pyproject.toml (BSR reads TOML config; keep package.json as the
    # version target)
    [tool.semantic_release]
    tag_format = "v{version}"

    [tool.semantic_release.bsr]
    schema_version = 1

    [[tool.semantic_release.bsr.version.sources]]
    kind = "git-tag"

    [[tool.semantic_release.bsr.version.targets]]
    kind = "json"
    path = "package.json"
    field = "version"

    [[tool.semantic_release.bsr.publish.publishers]]
    kind = "npm"

    [[tool.semantic_release.bsr.hooks]]
    point = "pre_publish"
    command = ["npm", "run", "build"]

Notes:

- npm is a provenance-capable target: run inside CI with OIDC enabled so the
  readiness probe reports trusted publishing.
- Tags follow ``v1.2.3`` via ``tag_format``; the version source reads the
  latest matching tag.
