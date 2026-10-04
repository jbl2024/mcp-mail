"""Deployment command contracts with isolated fake installers and interpreters."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def deployment(tmp_path):
    root = tmp_path / "example checkout"
    (root / "scripts").mkdir(parents=True)
    for name in ("install.sh", "run.sh"):
        shutil.copyfile(PROJECT_ROOT / "scripts" / name, root / "scripts" / name)
    (root / "uv.lock").write_text("fictional lock fixture")
    commands = tmp_path / "commands"
    commands.mkdir()
    installer = commands / "uv"
    installer.write_text("""#!/bin/sh
printf 'uv:%s\\n' "$*" >> "$INSTALL_TEST_LOG"
[ "${FAIL_SYNC:-0}" = 0 ] || exit 1
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
cat > "$UV_PROJECT_ENVIRONMENT/bin/python" <<'EOF'
#!/bin/sh
printf 'python:%s\\ncwd:%s\\n' "$*" "$PWD" >> "$INSTALL_TEST_LOG"
[ "${FAIL_IMPORT:-0}" = 0 ] || exit 1
EOF
chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
""")
    installer.chmod(0o755)
    log = tmp_path / "calls.log"
    env = os.environ | {"PATH": f"{commands}:{os.environ['PATH']}", "INSTALL_TEST_LOG": str(log)}
    return root, log, env


def invoke(root, script, env, cwd=None):
    return subprocess.run(
        ["sh", str(root / "scripts" / script)],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_install_pins_environment_and_verifies_imports(deployment, tmp_path):
    root, log, env = deployment
    env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "unrelated-environment")
    result = invoke(root, "install.sh", env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    calls = log.read_text()
    assert "--locked --no-dev --no-editable" in calls
    assert "--reinstall-package mcp-mail" in calls
    assert "from mcp_mail.server import mcp" in calls
    assert (root / ".venv/bin/python").is_file()
    assert not (tmp_path / "unrelated-environment").exists()
    assert result.stdout == ""


def test_launch_is_independent_of_uv_and_working_directory(deployment, tmp_path):
    root, log, env = deployment
    assert invoke(root, "install.sh", env).returncode == 0
    log.write_text("")
    (tmp_path / "commands/uv").unlink()
    result = invoke(root, "run.sh", env, cwd=tmp_path)
    assert result.returncode == 0
    assert log.read_text() == f"python:-B -m mcp_mail\ncwd:{root}\n"
    assert result.stdout == "" and result.stderr == ""


def test_launch_missing_environment_explains_installation(deployment):
    root, _, env = deployment
    result = invoke(root, "run.sh", env)
    assert result.returncode != 0
    assert "scripts/install.sh first" in result.stderr
    assert result.stdout == ""


def test_install_failure_does_not_launch_or_delete_environment(deployment):
    root, log, env = deployment
    (root / ".venv").mkdir()
    marker = root / ".venv/preserve.txt"
    marker.write_text("keep existing files")
    result = invoke(root, "install.sh", env | {"FAIL_SYNC": "1"})
    assert result.returncode != 0
    assert "permissions" in result.stderr
    assert "python:" not in log.read_text()
    assert marker.read_text() == "keep existing files"


def test_failed_import_is_not_reported_as_installed(deployment):
    root, _, env = deployment
    result = invoke(root, "install.sh", env | {"FAIL_IMPORT": "1"})
    assert result.returncode != 0
    assert "installation is incomplete" in result.stderr
    assert "Installation verified" not in result.stderr


def test_missing_lock_is_rejected_before_sync(deployment):
    root, log, env = deployment
    (root / "uv.lock").unlink()
    result = invoke(root, "install.sh", env)
    assert result.returncode != 0 and "uv.lock is missing" in result.stderr
    assert not log.exists()
