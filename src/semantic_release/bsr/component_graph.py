"""
Component dependency graph (Task 6, monorepo-lite).

Path-based planning decides which components changed; this module adds the
optional second step: components that *depend on* a changed component release
too. The graph is intentionally tiny -- names plus edges, parsed from config,
cycle-checked at build time, propagated as a deterministic closure.

No workspace tooling, no build orchestration: propagation only widens the
would-release set. Everything else stays with the existing plan/verify flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from semantic_release.errors import InvalidConfiguration


@dataclass(frozen=True)
class ComponentNode:
    """One component in the dependency graph."""

    name: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentGraph:
    """Validated component dependency graph (acyclic, names normalized)."""

    nodes: tuple[ComponentNode, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(node.name for node in self.nodes)

    def dependencies_of(self, name: str) -> tuple[str, ...]:
        """Direct dependencies declared for ``name`` (empty when unknown)."""
        for node in self.nodes:
            if node.name == name:
                return node.depends_on
        return ()

    def dependents_of(self, name: str) -> tuple[str, ...]:
        """Components that directly depend on ``name`` (stable order)."""
        return tuple(
            node.name for node in self.nodes if name in node.depends_on
        )


def _normalize_names(names: Iterable[str], field: str) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for raw in names:
        name = str(raw).strip()
        if not name:
            raise InvalidConfiguration(f"component graph: empty name in {field}")
        seen.setdefault(name)
    return tuple(seen)


def _parse_graph_entry(
    entry: object, index: int, names: set[str]
) -> ComponentNode:
    """Parse and validate one component_graph table entry."""
    if not isinstance(entry, Mapping):
        raise InvalidConfiguration(
            f"component graph: entry {index} must be a table"
        )
    raw_name = entry.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise InvalidConfiguration(
            f"component graph: entry {index} needs a non-empty 'name'"
        )
    name = raw_name.strip()
    if name in names:
        raise InvalidConfiguration(
            f"component graph: duplicate component {name!r}"
        )
    raw_deps = entry.get("depends_on", ())
    if isinstance(raw_deps, str):
        deps: tuple[str, ...] = (raw_deps,)
    elif isinstance(raw_deps, (list, tuple)):
        deps = _normalize_names(
            (str(item) for item in raw_deps),
            f"component {name!r} depends_on",
        )
    else:
        raise InvalidConfiguration(
            f"component graph: {name!r} depends_on must be a list"
        )
    return ComponentNode(name=name, depends_on=deps)


def build_component_graph(
    raw: Iterable[Mapping[str, object]] | None,
) -> ComponentGraph | None:
    """
    Parse the optional ``component_graph`` config list into a graph.

    Each entry: ``{"name": str, "depends_on": [str, ...]}``. Unknown
    dependencies and cycles fail closed with :class:`InvalidConfiguration`;
    ``None``/empty input builds ``None`` (feature off).
    """
    if raw is None:
        return None
    entries = list(raw)
    if not entries:
        return None

    nodes: list[ComponentNode] = []
    names: set[str] = set()
    for index, entry in enumerate(entries):
        node = _parse_graph_entry(entry, index, names)
        names.add(node.name)
        nodes.append(node)

    # Cross-reference and cycle-check (fail closed before any planning).
    for node in nodes:
        for dep in node.depends_on:
            if dep not in names:
                raise InvalidConfiguration(
                    f"component graph: {node.name!r} depends on unknown "
                    f"component {dep!r}"
                )
        if node.name in node.depends_on:
            raise InvalidConfiguration(
                f"component graph: {node.name!r} depends on itself"
            )

    graph = ComponentGraph(nodes=tuple(nodes))
    for node in nodes:
        _assert_acyclic(graph, node.name, origin=node.name, seen={node.name})
    return graph


def _assert_acyclic(
    graph: ComponentGraph, current: str, *, origin: str, seen: set[str]
) -> None:
    for dep in graph.dependencies_of(current):
        if dep == origin:
            raise InvalidConfiguration(
                f"component graph: dependency cycle through {origin!r}"
            )
        if dep in seen:
            continue
        seen.add(dep)
        _assert_acyclic(graph, dep, origin=origin, seen=seen)


def propagate_releases(
    changed: Iterable[str],
    graph: ComponentGraph | None,
) -> tuple[str, ...]:
    """
    Expand ``changed`` component ids with everything that (transitively)
    depends on them. Order is deterministic: declaration order first, then
    dependency depth. Duplicates collapse; unchanged components keep their
    relative order.
    """
    base = _normalize_names(changed, "changed components")
    if graph is None or not base:
        return base

    ordered_names = graph.names
    in_changed = set(base)
    result: list[str] = [name for name in ordered_names if name in in_changed]

    # Fixed-point widening: any component that depends on a releasing
    # component releases too.
    released = set(result)
    changed_this_round = True
    while changed_this_round:
        changed_this_round = False
        for node in graph.nodes:
            if node.name in released:
                continue
            if any(dep in released for dep in node.depends_on):
                released.add(node.name)
                result.append(node.name)
                changed_this_round = True

    # Names outside the graph pass through unchanged (path-map owns truth)
    # and sort after every graph component for determinism.
    known = set(ordered_names)
    result.extend(name for name in base if name not in known)
    return tuple(result)


__all__ = [
    "ComponentGraph",
    "ComponentNode",
    "build_component_graph",
    "propagate_releases",
]
