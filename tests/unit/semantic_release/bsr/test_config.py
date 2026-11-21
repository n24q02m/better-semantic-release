from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from semantic_release.bsr.component_map import ComponentPathMap
from semantic_release.bsr.config import BsrComponent, BsrConfig, load_bsr_config
from semantic_release.errors import InvalidConfiguration

if TYPE_CHECKING:
    from pathlib import Path


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "pyproject.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_defaults_when_no_bsr_table(tmp_path: Path):
    cfg_file = _write(tmp_path, '[tool.semantic_release]\ntag_format = "v{version}"\n')
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(
        guard_orphan_tag=True,
        guard_registry_collision=True,
        registry="",
        path_filter=False,
        paths=(),
        explain=False,
        actionable_errors=False,
    )


def test_reads_bsr_table(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n"
        "guard_registry_collision = true\n"
        'registry = "npm"\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(
        guard_orphan_tag=False,
        guard_registry_collision=True,
        registry="npm",
        path_filter=False,
        paths=(),
    )


def test_reads_path_filter_and_paths(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "path_filter = true\n"
        'paths = ["apps/api", "libs/x"]\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg == BsrConfig(path_filter=True, paths=("apps/api", "libs/x"))


def test_path_filter_and_paths_default_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.path_filter is False
    assert cfg.paths == ()


def test_reads_explain_and_actionable_errors(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "explain = true\n"
        "actionable_errors = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.explain is True
    assert cfg.actionable_errors is True


def test_explain_and_actionable_errors_default_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.explain is False
    assert cfg.actionable_errors is False


def test_missing_file_returns_defaults(tmp_path: Path):
    assert load_bsr_config(tmp_path / "nope.toml") == BsrConfig()


def test_malformed_config_fails_closed(tmp_path: Path):
    cfg_file = _write(tmp_path, "this is not valid toml :::\n")

    with pytest.raises(InvalidConfiguration):
        load_bsr_config(cfg_file)


def test_reads_summary_flag(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.summary is True


def test_summary_defaults_false_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.summary is False
    assert cfg.components == ()


def test_reads_components_table(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n"
        "[[tool.semantic_release.bsr.components]]\n"
        'name = "api"\n'
        'paths = ["apps/api"]\n'
        "[[tool.semantic_release.bsr.components]]\n"
        'name = "web"\n'
        'paths = ["apps/web", "libs/shared"]\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.components == (
        BsrComponent(name="api", paths=("apps/api",)),
        BsrComponent(name="web", paths=("apps/web", "libs/shared")),
    )


def test_components_default_empty_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "summary = true\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.components == ()


def test_reads_stable_notes_flags(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "stable_notes_aggregate = true\n"
        'stable_notes_scope = "since_stable"\n',
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.stable_notes_aggregate is True
    assert cfg.stable_notes_scope == "since_stable"


def test_invalid_stable_notes_scope_type_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'stable_notes_scope = ["line"]\n',
    )

    with pytest.raises(InvalidConfiguration, match="stable_notes_scope"):
        load_bsr_config(cfg_file)


def test_stable_notes_defaults_when_absent(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_orphan_tag = false\n",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.stable_notes_aggregate is False
    assert cfg.stable_notes_scope == "line"


def test_reads_versioned_component_path_map(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.component_path_map]\n"
        "schema_version = 1\n"
        'shared_policy = "none"\n'
        'root_policy = "none"\n'
        "[[tool.semantic_release.bsr.component_path_map.components]]\n"
        'id = "api"\n'
        'roots = ["apps/api"]\n'
        'release_paths = ["apps/api/pyproject.toml"]\n'
        'config_path = "apps/api/pyproject.toml"\n'
        "[[tool.semantic_release.bsr.component_path_map.rules]]\n"
        'kind = "component"\n'
        'path = "apps/api"\n'
        'components = ["api"]\n',
    )

    cfg = load_bsr_config(cfg_file)
    assert isinstance(cfg.component_path_map, ComponentPathMap)
    assert cfg.component_path_map.component_ids == ("api",)


def test_explicit_bsr_table_requires_schema_version(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "path_filter = true\n",
    )

    with pytest.raises(InvalidConfiguration, match="schema_version"):
        load_bsr_config(cfg_file)


def test_unknown_bsr_field_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "future_field = true\n",
    )

    with pytest.raises(InvalidConfiguration, match="future_field"):
        load_bsr_config(cfg_file)


def test_invalid_bsr_boolean_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'path_filter = "true"\n',
    )

    with pytest.raises(InvalidConfiguration, match="path_filter"):
        load_bsr_config(cfg_file)


def test_invalid_bsr_paths_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        'paths = ["apps/api", 7]\n',
    )

    with pytest.raises(InvalidConfiguration, match="paths"):
        load_bsr_config(cfg_file)


def test_invalid_component_path_map_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr.component_path_map]\n"
        "schema_version = 2\n",
    )

    with pytest.raises(InvalidConfiguration, match="schema_version"):
        load_bsr_config(cfg_file)


def test_loads_version_source_and_target_tables(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[[tool.semantic_release.bsr.version.sources]]\n"
        'kind = "json"\n'
        'path = "package.json"\n'
        'field = "version"\n'
        "[[tool.semantic_release.bsr.version.sources]]\n"
        'kind = "text"\n'
        'path = "VERSION"\n'
        'pattern = "{version}"\n'
        "[[tool.semantic_release.bsr.version.targets]]\n"
        'kind = "git-tag"\n',
        encoding="utf-8",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.version is not None
    assert [(s.kind, s.path, s.field) for s in cfg.version.sources] == [
        ("json", "package.json", "version"),
        ("text", "VERSION", ""),
    ]
    assert cfg.version.sources[1].pattern == "{version}"
    assert [(t.kind,) for t in cfg.version.targets] == [("git-tag",)]


def test_version_unknown_kind_fails_closed(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[[tool.semantic_release.bsr.version.sources]]\n"
        'kind = "yaml"\n'
        'path = "x.yml"\n'
        'field = "version"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfiguration, match="kind"):
        load_bsr_config(cfg_file)


def test_version_missing_required_field_fails_closed(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[[tool.semantic_release.bsr.version.targets]]\n"
        'kind = "json"\n'
        'path = "package.json"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfiguration, match="requires"):
        load_bsr_config(cfg_file)


def test_loads_publish_probe_and_publishers(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.publish.probe]\n"
        'kind = "github-release"\n'
        'repo = "acme/widgets"\n'
        "[[tool.semantic_release.bsr.publish.publishers]]\n"
        'kind = "shell"\n'
        'name = "docs"\n'
        'command = ["./scripts/publish-docs.sh"]\n'
        "[[tool.semantic_release.bsr.publish.publishers]]\n"
        'kind = "github-release"\n'
        'manifest_path = "dist/manifest.json"\n'
        'workspace = ".bsr"\n',
        encoding="utf-8",
    )
    cfg = load_bsr_config(cfg_file)
    assert cfg.publish is not None
    assert cfg.publish.probe is not None
    assert cfg.publish.probe.kind == "github-release"
    assert cfg.publish.probe.repo == "acme/widgets"
    kinds = [p.kind for p in cfg.publish.publishers]
    assert kinds == ["shell", "github-release"]
    assert cfg.publish.publishers[0].command == ("./scripts/publish-docs.sh",)


def test_publish_unknown_probe_kind_fails_closed(tmp_path: Path) -> None:
    cfg_file = tmp_path / "pyproject.toml"
    cfg_file.write_text(
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "[tool.semantic_release.bsr.publish.probe]\n"
        'kind = "gemfury"\n',
        encoding="utf-8",
    )
    with pytest.raises(InvalidConfiguration, match="probe.kind"):
        load_bsr_config(cfg_file)


def test_guard_tag_race_flag(tmp_path: Path):
    cfg_file = _write(
        tmp_path,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n"
        "guard_tag_race = false\n",
    )
    assert load_bsr_config(cfg_file).guard_tag_race is False
    tmp_path2 = tmp_path / "second"
    tmp_path2.mkdir()
    cfg_file2 = _write(
        tmp_path2,
        '[tool.semantic_release]\ntag_format = "v{version}"\n'
        "[tool.semantic_release.bsr]\n"
        "schema_version = 1\n",
    )
    assert load_bsr_config(cfg_file2).guard_tag_race is True


_BSR_HEADER = "[tool.semantic_release.bsr]\nschema_version = 1\n"
_PUBLISHER = "[[tool.semantic_release.bsr.publish.publishers]]\n"


@pytest.mark.parametrize(
    "body,message",
    [
        pytest.param(
            _BSR_HEADER + "components = 1\n",
            "components must be an array",
            id="components-not-array",
        ),
        pytest.param(
            _BSR_HEADER + "components = [1]\n",
            r"components\[0\] must be a table",
            id="component-not-table",
        ),
        pytest.param(
            _BSR_HEADER + 'components = [{ name = "api", owner = "x" }]\n',
            r"components\[0\] has unknown fields: owner",
            id="component-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + 'components = [{ name = " " }]\n',
            r"components\[0\]\.name must be a non-empty string",
            id="component-blank-name",
        ),
        pytest.param(
            _BSR_HEADER + 'components = [{ name = "api" }, { name = "api" }]\n',
            "duplicate component id: api",
            id="component-duplicate",
        ),
        pytest.param(
            _BSR_HEADER + 'components = [{ name = "api", paths = [""] }]\n',
            r"components\[0\]\.paths must be an array of strings",
            id="component-blank-path",
        ),
        pytest.param(
            _BSR_HEADER + "registry = 1\n",
            r"\.registry must be a string",
            id="registry-not-string",
        ),
        pytest.param(
            _BSR_HEADER + 'mode = "yolo"\n',
            r"\.mode must be one of: gated, release",
            id="unknown-mode",
        ),
        pytest.param(
            _BSR_HEADER + "gated = 1\n",
            r"\[tool\.semantic_release\.bsr\.gated\] must be a table",
            id="gated-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.gated]\nreviewers = 2\n",
            r"unknown \[tool\.semantic_release\.bsr\.gated\] fields: reviewers",
            id="gated-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + '[tool.semantic_release.bsr.gated]\nenvironment = " "\n',
            "bsr.gated.environment must be a non-empty string",
            id="gated-blank-environment",
        ),
        pytest.param(
            _BSR_HEADER + "component_graph = [1]\n",
            r"invalid \[tool\.semantic_release\.bsr\.component_graph\]: "
            "component graph: entry 0 must be a table",
            id="component-graph-entry-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.component_path_map]\n"
            "schema_version = 2\n",
            r"invalid \[tool\.semantic_release\.bsr\.component_path_map\]: "
            "component_path_map.schema_version must be 1",
            id="component-path-map-wrong-schema",
        ),
        pytest.param(
            _BSR_HEADER + "notes = 1\n",
            r"invalid \[tool\.semantic_release\.bsr\.notes\]",
            id="notes-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "hooks = 1\n",
            r"invalid \[tool\.semantic_release\.bsr\.hooks\]",
            id="hooks-not-table",
        ),
    ],
)
def test_invalid_bsr_tables_fail_closed(
    tmp_path: Path, body: str, message: str
) -> None:
    cfg_file = _write(tmp_path, body)

    with pytest.raises(InvalidConfiguration, match=message):
        load_bsr_config(cfg_file)


@pytest.mark.parametrize(
    "body,message",
    [
        pytest.param(
            _BSR_HEADER + "version = 1\n",
            r"\[tool\.semantic_release\.bsr\.version\] must be a table",
            id="version-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.version]\nformat = 1\n",
            r"unknown \[tool\.semantic_release\.bsr\.version\] fields: format",
            id="version-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.version]\nsources = 1\n",
            "bsr.version.sources must be an array of tables",
            id="sources-not-array",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.version]\ntargets = [1]\n",
            r"bsr\.version\.targets\[0\] must be a table",
            id="target-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[[tool.semantic_release.bsr.version.sources]]\n"
            'kind = "git-tag"\n'
            'regex = ".*"\n',
            r"bsr\.version\.sources\[0\] has unknown fields: regex",
            id="source-unknown-field",
        ),
    ],
)
def test_invalid_version_tables_fail_closed(
    tmp_path: Path, body: str, message: str
) -> None:
    cfg_file = _write(tmp_path, body)

    with pytest.raises(InvalidConfiguration, match=message):
        load_bsr_config(cfg_file)


@pytest.mark.parametrize(
    "body,message",
    [
        pytest.param(
            _BSR_HEADER + "publish = 1\n",
            r"\[tool\.semantic_release\.bsr\.publish\] must be a table",
            id="publish-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.publish]\ndry_run = true\n",
            r"unknown \[tool\.semantic_release\.bsr\.publish\] fields: dry_run",
            id="publish-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.publish]\nprobe = 1\n",
            "bsr.publish.probe must be a table",
            id="probe-not-table",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.publish.probe]\n"
            'kind = "pypi"\n'
            "timeout = 5\n",
            "bsr.publish.probe has unknown fields: timeout",
            id="probe-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.publish]\npublishers = 1\n",
            "bsr.publish.publishers must be an array of tables",
            id="publishers-not-array",
        ),
        pytest.param(
            _BSR_HEADER + "[tool.semantic_release.bsr.publish]\npublishers = [1]\n",
            r"bsr\.publish\.publishers\[0\] must be a table",
            id="publisher-not-table",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "shell"\nretries = 2\n',
            r"publishers\[0\] has unknown fields: retries",
            id="publisher-unknown-field",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "ftp"\n',
            r"publishers\[0\]\.kind must be one of",
            id="publisher-unknown-kind",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "shell"\ncommand = "make publish"\n',
            r"publishers\[0\]\.command must be an array of strings",
            id="publisher-command-not-array",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "oci"\nimage = 1\n',
            r"publishers\[0\]\.image must be a string",
            id="oci-image-not-string",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "oci"\nimage = "ghcr.io/a/b"\n'
            'tags = "latest"\n',
            r"publishers\[0\]\.tags must be an array of strings",
            id="oci-tags-not-array",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "oci"\nimage = "ghcr.io/a/b"\n'
            "dockerfile = 1\n",
            r"publishers\[0\]\.dockerfile must be a string",
            id="oci-dockerfile-not-string",
        ),
        pytest.param(
            _BSR_HEADER + _PUBLISHER + 'kind = "oci"\nimage = "ghcr.io/a/b"\n'
            'push = "yes"\n',
            r"publishers\[0\]\.push must be a boolean",
            id="oci-push-not-boolean",
        ),
    ],
)
def test_invalid_publish_tables_fail_closed(
    tmp_path: Path, body: str, message: str
) -> None:
    cfg_file = _write(tmp_path, body)

    with pytest.raises(InvalidConfiguration, match=message):
        load_bsr_config(cfg_file)


def test_loads_oci_publisher_fields(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        _BSR_HEADER + _PUBLISHER + 'kind = "oci"\n'
        'image = "ghcr.io/acme/widget"\n'
        'tags = ["{version}", "latest"]\n'
        'dockerfile = "docker/Dockerfile"\n'
        'context = "services/widget"\n'
        "push = true\n"
        'output = "dist/widget.oci.tar"\n',
    )

    cfg = load_bsr_config(cfg_file)

    assert cfg.publish is not None
    assert cfg.publish.probe is None
    [publisher] = cfg.publish.publishers
    assert publisher.kind == "oci"
    assert publisher.image == "ghcr.io/acme/widget"
    assert publisher.tags == ("{version}", "latest")
    assert publisher.dockerfile == "docker/Dockerfile"
    assert publisher.context == "services/widget"
    assert publisher.push is True
    assert publisher.output == "dist/widget.oci.tar"


def test_loads_gated_mode_and_environment(tmp_path: Path) -> None:
    cfg_file = _write(
        tmp_path,
        _BSR_HEADER + 'mode = "gated"\n'
        "[tool.semantic_release.bsr.gated]\n"
        'environment = "prod-approval"\n',
    )

    cfg = load_bsr_config(cfg_file)

    assert cfg.mode == "gated"
    assert cfg.gated is not None
    assert cfg.gated.environment == "prod-approval"


def test_gated_environment_defaults_when_omitted(tmp_path: Path) -> None:
    from semantic_release.bsr.gating import DEFAULT_GATE_ENVIRONMENT

    cfg_file = _write(
        tmp_path, _BSR_HEADER + 'mode = "gated"\n[tool.semantic_release.bsr.gated]\n'
    )

    cfg = load_bsr_config(cfg_file)

    assert cfg.gated is not None
    assert cfg.gated.environment == DEFAULT_GATE_ENVIRONMENT


def test_bsr_value_that_is_not_a_table_fails_closed(tmp_path: Path) -> None:
    cfg_file = _write(tmp_path, "[tool.semantic_release]\nbsr = 1\n")

    with pytest.raises(InvalidConfiguration, match=r"bsr\] must be a table"):
        load_bsr_config(cfg_file)


def test_json_config_that_is_not_a_table_fails_closed(tmp_path: Path) -> None:
    cfg_file = tmp_path / "releaserc.json"
    cfg_file.write_text('{"semantic_release": ["not", "a", "table"]}', encoding="utf-8")

    with pytest.raises(InvalidConfiguration, match="configuration must be a table"):
        load_bsr_config(cfg_file)


def test_unparseable_config_shape_is_reported_against_the_file(
    tmp_path: Path,
) -> None:
    cfg_file = tmp_path / "releaserc.json"
    cfg_file.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(InvalidConfiguration) as excinfo:
        load_bsr_config(cfg_file)

    assert str(excinfo.value).startswith(f"{cfg_file}: ")
    assert isinstance(excinfo.value.__cause__, TypeError)
