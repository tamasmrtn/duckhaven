"""scripts/check-dco.sh, the DCO gate CI runs on every pull request.

Each commit in the range needs a Signed-off-by trailer whose email matches the
commit's author; merge commits and bot commits (Dependabot) are exempt.
"""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-dco.sh"

ALICE = ("Alice", "alice@example.com")
BOT = ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com")


def git(repo, *args, author=ALICE):
    name, email = author
    env = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "HOME": str(repo),
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo, message, author=ALICE):
    git(repo, "commit", "--allow-empty", "-m", message, author=author)


def check(repo):
    return subprocess.run([str(SCRIPT), "base", "HEAD"], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    commit(tmp_path, "chore: Initial commit")
    git(tmp_path, "tag", "base")
    return tmp_path


def test_signed_off_commits_pass(repo):
    commit(repo, "feat: Add a thing\n\nSigned-off-by: Alice <alice@example.com>")
    commit(repo, "fix: Fix the thing\n\nSigned-off-by: Alice <ALICE@example.com>")

    result = check(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_commit_without_sign_off_fails_and_is_named(repo):
    commit(repo, "feat: Add a thing\n\nSigned-off-by: Alice <alice@example.com>")
    commit(repo, "fix: Forget the sign-off")

    result = check(repo)

    assert result.returncode == 1
    assert "Forget the sign-off" in result.stdout
    assert "Add a thing" not in result.stdout
    assert "git rebase --signoff" in result.stderr


def test_a_sign_off_by_someone_else_fails(repo):
    commit(repo, "feat: Add a thing\n\nSigned-off-by: Bob <bob@example.com>")

    assert check(repo).returncode == 1


def test_bot_commits_are_exempt(repo):
    commit(repo, "chore(deps): Bump a dependency", author=BOT)

    assert check(repo).returncode == 0


def test_merge_commits_are_exempt(repo):
    git(repo, "checkout", "-q", "-b", "topic")
    commit(repo, "feat: Add a thing\n\nSigned-off-by: Alice <alice@example.com>")
    git(repo, "checkout", "-q", "main")
    commit(repo, "fix: Other work\n\nSigned-off-by: Alice <alice@example.com>")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge topic", "topic")

    assert check(repo).returncode == 0
