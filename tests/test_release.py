from __future__ import annotations

import os
import subprocess
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT = PROJECT_ROOT / "scripts" / "release.sh"


def run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=True, text=True, capture_output=True)


def git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return run(["git", *arguments], cwd)


def test_release_updates_changelog_pushes_and_disambiguates_daily_tags(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repository = tmp_path / "repository"
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "init", "--initial-branch=main", str(repository))
    git(repository, "config", "user.name", "Release Bot")
    git(repository, "config", "user.email", "release-bot@example.test")
    git(repository, "remote", "add", "origin", str(remote))

    (repository / "Makefile").write_text("test:\n\t@:\n", encoding="utf-8")
    (repository / "application.txt").write_text("first change\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "feat: first change")
    git(repository, "push", "-u", "origin", "main")

    environment = os.environ | {"RELEASE_TEST_TARGET": "test"}
    subprocess.run(
        ["sh", str(RELEASE_SCRIPT)],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )

    today = date.today().strftime("%Y%m%d")
    assert git(repository, "tag", "--list", today).stdout.strip() == today
    changelog = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{today}] - {date.today():%Y-%m-%d}" in changelog
    assert "- feat: first change" in changelog
    assert git(tmp_path, "--git-dir", str(remote), "tag", "--list", today).stdout.strip() == today

    (repository / "application.txt").write_text("second change\n", encoding="utf-8")
    git(repository, "add", "application.txt")
    git(repository, "commit", "-m", "fix: second change")
    subprocess.run(
        ["sh", str(RELEASE_SCRIPT)],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )

    second_tag = f"{today}-1"
    assert git(repository, "tag", "--list", second_tag).stdout.strip() == second_tag
    assert (
        git(tmp_path, "--git-dir", str(remote), "tag", "--list", second_tag).stdout.strip()
        == second_tag
    )
    changelog = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{second_tag}] - {date.today():%Y-%m-%d}" in changelog
    assert "- fix: second change" in changelog
