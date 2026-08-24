# ruff: noqa: S603, S607
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_SCRIPT = REPO_ROOT / "src" / "gh_action" / "action.sh"


def _write_executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    git_exec_path = Path(
        subprocess.run(
            ["git", "--exec-path"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    bash = git_exec_path.parents[2] / "bin" / "bash.exe"
    assert bash.is_file()
    return str(bash)


def _msys_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive
    if not drive:
        return resolved.as_posix()
    return f"/{drive[0].lower()}{resolved.as_posix()[2:]}"


def test_action_creates_private_ssh_key_with_restrictive_mode(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "semantic-release", "exit 0\n")

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    bash = _bash_executable()
    env = {
        **os.environ,
        "HOME": _msys_path(home),
        "PSR_VENV_BIN": _msys_path(fake_bin),
        "INPUT_VERBOSITY": "0",
        "INPUT_CONFIG_FILE": "",
        "INPUT_STRICT": "false",
        "INPUT_NO_OPERATION_MODE": "true",
        "INPUT_PRERELEASE": "",
        "INPUT_COMMIT": "",
        "INPUT_TAG": "",
        "INPUT_PUSH": "",
        "INPUT_CHANGELOG": "",
        "INPUT_VCS_RELEASE": "",
        "INPUT_BUILD": "",
        "INPUT_FORCE": "",
        "INPUT_BUILD_METADATA": "",
        "INPUT_PRERELEASE_TOKEN": "",
        "INPUT_DIRECTORY": _msys_path(workspace),
        "INPUT_GIT_COMMITTER_NAME": "Test User",
        "INPUT_GIT_COMMITTER_EMAIL": "test@example.com",
        "INPUT_SSH_PUBLIC_SIGNING_KEY": "ssh-ed25519 public-key",
        "INPUT_SSH_PRIVATE_SIGNING_KEY": "line-one\\nline-two",
        "INPUT_GITHUB_TOKEN": "test-token",
        "EXPECTED_PRIVATE_KEY_MODE": "644" if os.name == "nt" else "600",
    }
    wrapper = f"""
git() {{ return 0; }}
sha256sum() {{ return 0; }}
ssh-agent() {{
    printf '%s\\n' \
        'SSH_AUTH_SOCK=/tmp/fake; export SSH_AUTH_SOCK;' \
        'SSH_AGENT_PID=1; export SSH_AGENT_PID;' \
        'echo Agent pid 1;'
}}
chmod() {{
    if [[ "$*" == *"/signing_key"* ]]; then
        printf '%s\n' 'private key permissions were repaired after creation' >&2
        return 97
    fi
    command chmod "$@"
}}
ssh-add() {{
    local mode
    mode=$(stat -c '%a' "$1")
    if [ "$mode" != "$EXPECTED_PRIVATE_KEY_MODE" ]; then
        printf 'private key mode at use was %s, expected %s\n' \
            "$mode" "$EXPECTED_PRIVATE_KEY_MODE" >&2
        return 98
    fi
}}
source '{ACTION_SCRIPT.as_posix()}'
"""
    result = subprocess.run(
        [bash, "-c", wrapper],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    private_key = home / ".ssh" / "signing_key"
    mode = subprocess.run(
        [bash, "-c", f"stat -c '%a' '{_msys_path(private_key)}'"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    assert mode == env["EXPECTED_PRIVATE_KEY_MODE"]
    assert private_key.read_text(encoding="utf-8") == "line-one\nline-two\n"
