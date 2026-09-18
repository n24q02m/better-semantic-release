"""Unit tests for finite hook points (Task 8)."""

from __future__ import annotations

import pytest

from semantic_release.bsr.hooks import (
    HOOK_POINTS,
    HookSpec,
    parse_hooks,
    run_hooks,
)
from semantic_release.errors import InvalidConfiguration


def test_hook_points_are_finite() -> None:
    assert HOOK_POINTS == (
        "pre_plan",
        "post_plan",
        "pre_verify",
        "pre_publish",
        "post_publish",
    )


def test_parse_hooks_preserves_order() -> None:
    specs = parse_hooks(
        [
            {"point": "pre_publish", "command": ["a"]},
            {"point": "pre_plan", "command": ["b"]},
            {"point": "pre_publish", "command": ["c"]},
        ]
    )
    assert [(s.point, s.display_command) for s in specs] == [
        ("pre_publish", "a"),
        ("pre_plan", "b"),
        ("pre_publish", "c"),
    ]


def test_parse_hooks_unknown_point_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="unknown point"):
        parse_hooks([{"point": "mid_publish", "command": ["x"]}])


def test_parse_hooks_empty_command_fails_closed() -> None:
    with pytest.raises(InvalidConfiguration, match="non-empty list"):
        parse_hooks([{"point": "pre_publish"}])


def test_parse_hooks_none_is_no_hooks() -> None:
    assert parse_hooks(None) == ()


def test_run_hooks_only_fires_matching_point(tmp_path) -> None:
    ran: list[str] = []

    def fake_run(argv, **kwargs):
        ran.append(argv[0])
        return __import__("subprocess").CompletedProcess(argv, 0)

    hooks = (
        HookSpec(point="pre_plan", command=["plan-hook"]),
        HookSpec(point="pre_publish", command=["publish-hook"]),
    )
    import semantic_release.bsr.hooks as hooks_mod

    original = hooks_mod.subprocess.run
    hooks_mod.subprocess.run = fake_run
    try:
        ok, detail = run_hooks(hooks, "pre_publish", repo_dir=tmp_path)
    finally:
        hooks_mod.subprocess.run = original
    assert ok is True
    assert ran == ["publish-hook"]
    assert detail == ""


def test_run_hooks_failure_blocks_remaining(tmp_path) -> None:
    order: list[str] = []

    def fake_run(argv, **kwargs):
        order.append(argv[0])
        code = 1 if argv[0] == "failing" else 0
        import subprocess

        return subprocess.CompletedProcess(argv, code)

    hooks = (
        HookSpec(point="pre_publish", command=["failing"]),
        HookSpec(point="pre_publish", command=["never-runs"]),
    )
    import semantic_release.bsr.hooks as hooks_mod

    original = hooks_mod.subprocess.run
    hooks_mod.subprocess.run = fake_run
    try:
        ok, detail = run_hooks(hooks, "pre_publish", repo_dir=tmp_path)
    finally:
        hooks_mod.subprocess.run = original
    assert ok is False
    assert order == ["failing"]
    assert "exited 1" in detail


def test_run_hooks_real_command_success_and_failure(tmp_path) -> None:
    ok_hook = HookSpec(
        point="pre_verify", command=["python", "-c", "print('hook ran')"]
    )
    ok, detail = run_hooks((ok_hook,), "pre_verify", repo_dir=tmp_path)
    assert ok is True

    bad_hook = HookSpec(
        point="pre_verify", command=["python", "-c", "import sys; sys.exit(2)"]
    )
    ok, detail = run_hooks((bad_hook,), "pre_verify", repo_dir=tmp_path)
    assert ok is False
    assert "exited 2" in detail
