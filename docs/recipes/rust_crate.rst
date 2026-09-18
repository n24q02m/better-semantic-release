.. _bsr-recipe-rust:

Releasing a Rust crate or small workspace
=========================================

BSR bumps the crate version, delegates the build to cargo via a hook, and
publishes through crates.io.

.. code-block:: toml

    [tool.semantic_release]
    tag_format = "v{version}"

    [tool.semantic_release.bsr]
    schema_version = 1

    [[tool.semantic_release.bsr.version.sources]]
    kind = "git-tag"

    [[tool.semantic_release.bsr.version.targets]]
    kind = "toml"
    path = "Cargo.toml"
    field = "package.version"

    [[tool.semantic_release.bsr.publish.publishers]]
    kind = "crates"

    [[tool.semantic_release.bsr.hooks]]
    point = "pre_publish"
    command = ["cargo", "build", "--release"]

Notes:

- crates.io publishing reads ``CARGO_REGISTRY_TOKEN``; the readiness probe
  fails closed when it is missing.
- For a workspace, list one version target per member crate.
