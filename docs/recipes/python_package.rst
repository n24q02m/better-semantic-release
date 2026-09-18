.. _bsr-recipe-python:

Releasing a Python package
==========================

BSR is a fork of python-semantic-release: an existing PSR configuration keeps
working. Add a ``[tool.semantic_release.bsr]`` table to opt in to BSR-only
features.

.. code-block:: toml

    [tool.semantic_release]
    version_toml = ["pyproject.toml:project.version"]
    build_command = "python -m pip wheel . -w dist"

    [tool.semantic_release.branches.main]
    match = "main"

    [tool.semantic_release.bsr]
    schema_version = 1

    [[tool.semantic_release.bsr.version.sources]]
    kind = "git-tag"

    [[tool.semantic_release.bsr.version.targets]]
    kind = "toml"
    path = "pyproject.toml"
    field = "project.version"

    [tool.semantic_release.bsr.publish.probe]
    kind = "github-release"
    repo = "acme/widgets"

    [[tool.semantic_release.bsr.publish.publishers]]
    kind = "pypi"

    [[tool.semantic_release.bsr.hooks]]
    point = "pre_publish"
    command = ["python", "-m", "build", "--sdist", "--wheel"]

Notes:

- Publishing expects a trusted-publishing (OIDC) environment. See
  :ref:`bsr-gated-release` for the readiness probe.
- The ``pre_publish`` hook is optional; it fails the run when it exits
  non-zero.
