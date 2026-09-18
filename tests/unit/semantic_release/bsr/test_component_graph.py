"""Unit tests for the component dependency graph (Task 6)."""

from __future__ import annotations

import pytest

from semantic_release.bsr.component_graph import (
    build_component_graph,
    propagate_releases,
)
from semantic_release.errors import InvalidConfiguration


def _graph(entries):
    return build_component_graph(entries)


def test_none_and_empty_build_none() -> None:
    assert build_component_graph(None) is None
    assert build_component_graph([]) is None


def test_parse_nodes_and_edges() -> None:
    graph = _graph(
        [
            {"name": "api", "depends_on": ["core", "sdk"]},
            {"name": "core"},
            {"name": "sdk", "depends_on": ["core"]},
        ]
    )
    assert graph is not None
    assert graph.names == ("api", "core", "sdk")
    assert graph.dependencies_of("api") == ("core", "sdk")
    assert graph.dependents_of("core") == ("api", "sdk")


def test_unknown_dependency_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="unknown component 'ghost'"):
        _graph([{"name": "api", "depends_on": ["ghost"]}])


def test_duplicate_component_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="duplicate"):
        _graph([{"name": "api"}, {"name": "api"}])


def test_self_dependency_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="depends on itself"):
        _graph([{"name": "api", "depends_on": ["api"]}])


def test_two_node_cycle_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="cycle through 'a'"):
        _graph(
            [
                {"name": "a", "depends_on": ["b"]},
                {"name": "b", "depends_on": ["a"]},
            ]
        )


def test_three_node_cycle_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="cycle"):
        _graph(
            [
                {"name": "a", "depends_on": ["b"]},
                {"name": "b", "depends_on": ["c"]},
                {"name": "c", "depends_on": ["a"]},
            ]
        )


def test_propagation_pulls_dependents() -> None:
    graph = _graph(
        [
            {"name": "app", "depends_on": ["sdk"]},
            {"name": "sdk", "depends_on": ["core"]},
            {"name": "core"},
            {"name": "unrelated"},
        ]
    )
    # core changed -> sdk and app release; unrelated untouched.
    assert propagate_releases(["core"], graph) == ("core", "sdk", "app")


def test_propagation_no_graph_is_identity() -> None:
    assert propagate_releases(["a", "b"], None) == ("a", "b")


def test_propagation_unknown_names_pass_through() -> None:
    graph = _graph([{"name": "app", "depends_on": ["core"]}, {"name": "core"}])
    assert propagate_releases(["core", "ghost"], graph) == ("core", "app", "ghost")


def test_propagation_empty_changed() -> None:
    graph = _graph([{"name": "a", "depends_on": ["b"]}, {"name": "b"}])
    assert propagate_releases([], graph) == ()


def test_propagation_transitive_widening() -> None:
    graph = _graph(
        [
            {"name": "top", "depends_on": ["mid"]},
            {"name": "mid", "depends_on": ["leaf"]},
            {"name": "leaf"},
        ]
    )
    assert propagate_releases(["leaf"], graph) == ("leaf", "mid", "top")


def test_longer_cycle_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="cycle"):
        _graph(
            [
                {"name": "a", "depends_on": ["b"]},
                {"name": "b", "depends_on": ["c"]},
                {"name": "c", "depends_on": ["d"]},
                {"name": "d", "depends_on": ["b"]},
            ]
        )


def test_build_summary_widens_would_release_via_graph(tmp_path) -> None:
    """A component with no own commits releases when its dependency changes."""
    from unittest import mock

    from semantic_release.bsr.summary import ComponentPlan, build_summary

    plans = [
        ComponentPlan(
            name="app",
            would_release=False,
            level="NO_CHANGE",
            commit_count=0,
            sample_paths=(),
            resulting_version="1.0.0",
        ),
        ComponentPlan(
            name="core",
            would_release=True,
            level="PATCH",
            commit_count=2,
            sample_paths=("core/x.py",),
            resulting_version="1.0.1",
        ),
    ]
    graph = build_component_graph(
        [{"name": "app", "depends_on": ["core"]}, {"name": "core"}]
    )

    with mock.patch(
        "semantic_release.bsr.summary.build_component_plan",
        side_effect=lambda component, **_kw: next(
            p for p in plans if p.name == component.name
        ),
    ):
        out = build_summary(
            [type("C", (), {"name": "app"})(), type("C", (), {"name": "core"})()],
            repo=mock.Mock(),
            translator=mock.Mock(),
            commit_parser=mock.Mock(),
            prerelease=False,
            major_on_zero=False,
            allow_zero_version=True,
            component_graph=graph,
        )

    assert [p.name for p in out] == ["app", "core"]
    assert out[0].would_release is True  # widened via dependency on core
    assert out[0].level == "NO_CHANGE"  # own facts unchanged
    assert out[1].would_release is True


def test_build_summary_without_graph_keeps_rows() -> None:
    from unittest import mock

    from semantic_release.bsr.summary import ComponentPlan, build_summary

    plan = ComponentPlan(
        name="solo",
        would_release=False,
        level="NO_CHANGE",
        commit_count=0,
        sample_paths=(),
        resulting_version="2.0.0",
    )
    with mock.patch(
        "semantic_release.bsr.summary.build_component_plan", return_value=plan
    ):
        out = build_summary(
            [type("C", (), {"name": "solo"})()],
            repo=mock.Mock(),
            translator=mock.Mock(),
            commit_parser=mock.Mock(),
            prerelease=False,
            major_on_zero=False,
            allow_zero_version=True,
        )
    assert out == (plan,)
