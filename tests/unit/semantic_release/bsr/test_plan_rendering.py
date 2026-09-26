"""
Byte-exact golden snapshots for every ``ReleasePlan`` render surface (W1.3).

The table, markdown, and schema-v1 JSON renders are the plan's contract with
consumers (see ``docs/api/plan-schema.rst``). These snapshots freeze their
exact output: a diff here MUST correspond to an intentional, documented
render/schema change — that is the drift guard between the rendered document
and the schema page.

Plan scenarios covered: release (bump stats), no-release (decision only),
blocked monorepo (components + blocker + registry row).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from semantic_release.bsr.plan import (
    Blocker,
    RegistryStatus,
    build_release_plan,
)

from tests.unit.semantic_release.bsr.test_plan import _bump_stats, _component, _decision

if TYPE_CHECKING:
    from semantic_release.bsr.plan import ReleasePlan


def _plan_release() -> ReleasePlan:
    return build_release_plan(
        released=True,
        version="1.3.0",
        tag="v1.3.0",
        previous_version="1.2.3",
        decision=None,
        bump_stats=_bump_stats(),
    )


def _plan_no_release() -> ReleasePlan:
    return build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=_decision(3),
        bump_stats=None,
    )


def _plan_blocked_monorepo() -> ReleasePlan:
    return build_release_plan(
        released=False,
        version=None,
        tag=None,
        previous_version="1.2.3",
        decision=_decision(3),
        bump_stats=None,
        components=(
            _component("api", would_release=True, level="MINOR"),
            _component("lib", would_release=False, level="NONE"),
        ),
        blockers=(
            Blocker(
                code="ORPHAN_TAG",
                message="tag v1.2.3 is orphaned",
                remediation="re-tag",
            ),
        ),
        registry=RegistryStatus(
            registry="pypi", reachable=None, detail="probe skipped"
        ),
    )


# ---------------------------------------------------------------------------
# golden: render_table()
# (Line-list form: several table rows genuinely end in padding spaces, and
# literal multi-line strings would trip trailing-whitespace linting.)

_TABLE_RELEASE_LINES = [
    "better-semantic-release release plan",
    "  released:         yes",
    "  version:          1.3.0",
    "  tag:              v1.3.0",
    "  previous version: 1.2.3",
    "  reason:           -",
    "  level bump:       minor",
    "  commits:          7",
]

_TABLE_NO_RELEASE_LINES = [
    "better-semantic-release release plan",
    "  released:         no",
    "  version:          -",
    "  tag:              -",
    "  previous version: 1.2.3",
    "  reason:           NO_QUALIFYING_COMMITS",
    "  level bump:       -",
    "  commits:          3",
]

_TABLE_BLOCKED_LINES = [
    "better-semantic-release release plan",
    "  released:         no",
    "  version:          -",
    "  tag:              -",
    "  previous version: 1.2.3",
    "  reason:           NO_QUALIFYING_COMMITS",
    "  level bump:       -",
    "  commits:          3",
    "  blockers:",
    "    - [ORPHAN_TAG] tag v1.2.3 is orphaned (fix: re-tag)",
    "",
    "component  would-release  level  commits  sample paths  version",
    "---------  -------------  -----  -------  ------------  -------",
    "api        yes            MINOR  7        api/x.py      1.3.0  ",
    "lib        no             NONE   7        lib/x.py      1.3.0  ",
]


def test_golden_table_release() -> None:
    assert _plan_release().render_table() == "\n".join(_TABLE_RELEASE_LINES)


def test_golden_table_no_release() -> None:
    assert _plan_no_release().render_table() == "\n".join(_TABLE_NO_RELEASE_LINES)


def test_golden_table_blocked_monorepo() -> None:
    assert _plan_blocked_monorepo().render_table() == "\n".join(_TABLE_BLOCKED_LINES)


# ---------------------------------------------------------------------------
# golden: render_markdown()


_MARKDOWN_RELEASE_LINES = [
    "## Release plan",
    "",
    "| field | value |",
    "| --- | --- |",
    "| released | yes |",
    "| version | `1.3.0` |",
    "| tag | `v1.3.0` |",
    "| previous version | `1.2.3` |",
    "| reason | - |",
]

_MARKDOWN_NO_RELEASE_LINES = [
    "## Release plan",
    "",
    "| field | value |",
    "| --- | --- |",
    "| released | no |",
    "| version | `-` |",
    "| tag | `-` |",
    "| previous version | `1.2.3` |",
    "| reason | NO_QUALIFYING_COMMITS |",
]

_MARKDOWN_BLOCKED_LINES = [
    "## Release plan",
    "",
    "| field | value |",
    "| --- | --- |",
    "| released | no |",
    "| version | `-` |",
    "| tag | `-` |",
    "| previous version | `1.2.3` |",
    "| reason | NO_QUALIFYING_COMMITS |",
    "",
    "| component | would release | level | commits | resulting version |",
    "| --- | --- | --- | --- | --- |",
    "| api | yes | MINOR | 7 | `1.3.0` |",
    "| lib | no | NONE | 7 | `1.3.0` |",
    "",
    "### Blockers",
    "",
    "- **ORPHAN_TAG**: tag v1.2.3 is orphaned — *fix:* re-tag",
]


def test_golden_markdown_release() -> None:
    assert _plan_release().render_markdown() == "\n".join(_MARKDOWN_RELEASE_LINES)


def test_golden_markdown_no_release() -> None:
    assert _plan_no_release().render_markdown() == "\n".join(_MARKDOWN_NO_RELEASE_LINES)


def test_golden_markdown_blocked_monorepo() -> None:
    assert _plan_blocked_monorepo().render_markdown() == "\n".join(
        _MARKDOWN_BLOCKED_LINES
    )


# ---------------------------------------------------------------------------
# golden: schema-v1 JSON document serialization bytes
#
# The document dict shape is locked by test_plan.py; this locks the
# SERIALIZATION (indent, key order, escaping) that consumers byte-compare or
# hash in CI artifacts.

_JSON_BLOCKED_LINES = [
    "{",
    '  "schema_version": 1,',
    '  "released": false,',
    '  "version": null,',
    '  "tag": null,',
    '  "is_prerelease": false,',
    '  "previous_version": "1.2.3",',
    '  "decision": {',
    '    "code": "NO_QUALIFYING_COMMITS",',
    '    "commit_count": 3',
    "  },",
    '  "bump": {',
    '    "level_bump": null,',
    '    "commit_count": 3,',
    '    "type_counts": {}',
    "  },",
    '  "components": [',
    "    {",
    '      "name": "api",',
    '      "would_release": true,',
    '      "level": "MINOR",',
    '      "commit_count": 7,',
    '      "sample_paths": [',
    '        "api/x.py"',
    "      ],",
    '      "resulting_version": "1.3.0"',
    "    },",
    "    {",
    '      "name": "lib",',
    '      "would_release": false,',
    '      "level": "NONE",',
    '      "commit_count": 7,',
    '      "sample_paths": [',
    '        "lib/x.py"',
    "      ],",
    '      "resulting_version": "1.3.0"',
    "    }",
    "  ],",
    '  "blockers": [',
    "    {",
    '      "code": "ORPHAN_TAG",',
    '      "message": "tag v1.2.3 is orphaned",',
    '      "remediation": "re-tag"',
    "    }",
    "  ],",
    '  "registry": {',
    '    "registry": "pypi",',
    '    "reachable": null,',
    '    "detail": "probe skipped"',
    "  },",
    '  "publish_target": null',
    "}",
]

_JSON_RELEASE_LINES = [
    "{",
    '  "schema_version": 1,',
    '  "released": true,',
    '  "version": "1.3.0",',
    '  "tag": "v1.3.0",',
    '  "is_prerelease": false,',
    '  "previous_version": "1.2.3",',
    '  "decision": null,',
    '  "bump": {',
    '    "level_bump": "minor",',
    '    "commit_count": 7,',
    '    "type_counts": {',
    '      "feat": 2,',
    '      "fix": 5',
    "    }",
    "  },",
    '  "components": [],',
    '  "blockers": [],',
    '  "registry": null,',
    '  "publish_target": null',
    "}",
]


def test_golden_json_document_serialization_bytes() -> None:
    doc = _plan_blocked_monorepo().to_document()
    serialized = json.dumps(doc, indent=2)
    assert serialized == "\n".join(_JSON_BLOCKED_LINES)
    # The same bytes must round-trip through the parser consumers use.
    assert json.loads(serialized) == doc


def test_golden_json_document_bytes_release() -> None:
    doc = _plan_release().to_document()
    assert json.dumps(doc, indent=2) == "\n".join(_JSON_RELEASE_LINES)
