"""Pins scripts/upstream-sync.sh (the Gitea weekly upstream sync) and its schedule.

The script runs against throwaway local repos with DRY_RUN=1, so nothing is pushed and no forge API is called.
The GitHub release lookup is pointed at a file:// URL through UPSTREAM_RELEASE_API.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "upstream-sync.sh"
WORKFLOW = REPO / ".gitea" / "workflows" / "upstream-sync.yml"

pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("bash", "git", "curl", "jq")),
    reason="needs bash, git, curl and jq",
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def git(cwd, *args):
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, text):
    (Path(cwd) / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-qm", f"{name}: {text}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """upstream (c0 -> c1 tagged v1 -> c2 untagged tip) and a fork cloned at c0 with one commit of its own."""
    up = tmp_path / "upstream"
    up.mkdir()
    git(up, "init", "-qb", "main")
    c0 = commit(up, "base.txt", "base\n")
    fork_origin = tmp_path / "fork-origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(up), str(fork_origin))
    fork = tmp_path / "fork"
    git(tmp_path, "clone", "-q", str(fork_origin), str(fork))
    commit(fork, "fork-only.txt", "fork\n")
    git(fork, "push", "-q", "origin", "main")
    c1 = commit(up, "release.txt", "release\n")
    git(up, "tag", "-a", "v1", "-m", "v1")  # annotated: FETCH_HEAD is the tag object, not the commit
    c2 = commit(up, "unreleased.txt", "unreleased\n")
    return {"tmp": tmp_path, "up": up, "fork": fork, "c0": c0, "c1": c1, "c2": c2}


def run_sync(repos, release_json=None, **extra):
    env = {**os.environ, **GIT_ENV, "UPSTREAM_URL": str(repos["up"]), "UPSTREAM_BRANCH": "main",
           "BASE_BRANCH": "main", "DRY_RUN": "1"}
    env.pop("GITEA_API", None)
    if release_json is not None:
        api = repos["tmp"] / "latest.json"
        api.write_text(release_json)
        env.update(UPSTREAM_RELEASE="1", UPSTREAM_RELEASE_API=api.as_uri())
    env.update(extra)
    return subprocess.run(["bash", str(SCRIPT)], cwd=repos["fork"], env=env, capture_output=True, text=True)


def is_ancestor(repo, sha):
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=repo["fork"]).returncode == 0


def test_release_mode_merges_the_release_tag_not_the_branch_tip(repos):
    r = run_sync(repos, '{"tag_name": "v1"}')
    assert r.returncode == 0, r.stderr
    assert "DRY_RUN: would push upstream-sync/v1" in r.stdout
    assert git(repos["fork"], "branch", "--show-current") == "upstream-sync/v1"
    assert is_ancestor(repos, repos["c1"])
    assert not is_ancestor(repos, repos["c2"]), "unreleased upstream commits must not ride along"


def test_default_mode_still_merges_the_branch_tip(repos):
    r = run_sync(repos)
    assert r.returncode == 0, r.stderr
    assert f"upstream-sync/{repos['c2'][:8]}" in r.stdout
    assert is_ancestor(repos, repos["c2"])


def test_release_already_in_the_fork_is_a_quiet_no_op(repos):
    git(repos["fork"], "fetch", "-q", str(repos["up"]), "refs/tags/v1")
    git(repos["fork"], "merge", "-q", "--no-edit", "FETCH_HEAD")
    r = run_sync(repos, '{"tag_name": "v1"}')
    assert r.returncode == 0, r.stderr
    assert "up to date with upstream (v1)" in r.stdout
    assert git(repos["fork"], "branch", "--show-current") == "main"


def test_conflicting_release_is_reported_not_merged(repos):
    commit(repos["up"], "shared.txt", "upstream\n")
    git(repos["up"], "tag", "v2")
    # fork edits the same file differently, then fetches nothing from upstream
    commit(repos["fork"], "shared.txt", "fork\n")
    git(repos["fork"], "push", "-q", "origin", "main")
    r = run_sync(repos, '{"tag_name": "v2"}')
    assert r.returncode == 0, r.stderr
    assert "merge conflict with upstream v2" in r.stdout


@pytest.mark.parametrize("body", [
    '{"tag_name": "v1; touch pwned"}',
    '{"tag_name": "-v1"}',
    '{"tag_name": "a b"}',
    '{"tag_name": null}',
    '{"message": "Not Found"}',
    "not json",
])
def test_unusable_release_answer_fails_the_job_instead_of_guessing(repos, body):
    r = run_sync(repos, body)
    assert r.returncode != 0
    assert "DRY_RUN" not in r.stdout
    assert not (repos["fork"] / "pwned").exists()


def test_unreachable_release_api_fails_the_job(repos):
    r = run_sync(repos, '{"tag_name": "v1"}', UPSTREAM_RELEASE_API=(repos["tmp"] / "missing.json").as_uri())
    assert r.returncode != 0
    assert "DRY_RUN" not in r.stdout


def test_workflow_schedule_and_mode_are_pinned():
    text = WORKFLOW.read_text()
    # Gitea evaluates cron in UTC: Sunday 19:00 UTC is Monday 05:00 in Brisbane (UTC+10, no DST).
    assert re.search(r"^\s*- cron: '0 19 \* \* 0'", text, re.M)
    assert not re.search(r"TZ=", text.split("jobs:")[0].split("on:")[1].split("#")[0])
    assert re.search(r"^\s*UPSTREAM_RELEASE: '1'", text, re.M)
    assert "workflow_dispatch:" in text
