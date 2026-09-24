"""Spec for the fork-only GHCR publish workflow and the upstream guards it relies on."""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
FORK_REPO = "kmbrimble/soulsync-multiuser"
IMAGE = f"ghcr.io/{FORK_REPO}"


def _load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wf():
    return _load("fork-publish.yml")


@pytest.fixture(scope="module")
def job(wf):
    assert len(wf["jobs"]) == 1
    return next(iter(wf["jobs"].values()))


def _step(job, uses_prefix):
    matches = [s for s in job["steps"] if s.get("uses", "").startswith(uses_prefix)]
    assert len(matches) == 1, uses_prefix
    return matches[0]


def test_triggers_are_push_main_and_dispatch(wf):
    # PyYAML parses the bare key `on` as boolean True.
    triggers = wf.get("on", wf.get(True))
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_repo_guard_is_fork_not_upstream(job):
    assert job["if"] == f"github.repository == '{FORK_REPO}'"


def test_least_privilege_permissions_timeout_and_concurrency(wf, job):
    perms = job.get("permissions") or wf.get("permissions")
    assert perms == {"contents": "read", "packages": "write"}
    assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] > 0
    conc = wf["concurrency"]
    assert conc["group"]
    assert conc["cancel-in-progress"] is True


def test_action_versions_mirror_upstream(job):
    _step(job, "actions/checkout@v6")
    _step(job, "docker/setup-buildx-action@v4")
    _step(job, "docker/login-action@v4")
    _step(job, "docker/build-push-action@v7")
    assert not any("qemu" in s.get("uses", "") for s in job["steps"])


def test_login_is_ghcr_with_github_token_only(job):
    login = _step(job, "docker/login-action@")["with"]
    assert login["registry"] == "ghcr.io"
    assert login["password"] == "${{ secrets.GITHUB_TOKEN }}"


def test_build_is_amd64_only_with_commit_sha_and_gha_cache(job):
    w = _step(job, "docker/build-push-action@")["with"]
    assert w["platforms"] == "linux/amd64"
    assert str(w["push"]).lower() == "true"
    assert "COMMIT_SHA=${{ github.sha }}" in w["build-args"]
    assert w["cache-from"] == "type=gha"
    assert w["cache-to"] == "type=gha,mode=max"


def test_tags_are_latest_and_short_sha_on_ghcr_only(job):
    w = _step(job, "docker/build-push-action@")["with"]
    tags = [t.strip() for t in w["tags"].splitlines() if t.strip()]
    assert len(tags) == 2
    assert f"{IMAGE}:latest" in tags
    sha_tags = [t for t in tags if t != f"{IMAGE}:latest"]
    assert len(sha_tags) == 1 and sha_tags[0].startswith(f"{IMAGE}:sha-")
    assert sha_tags[0].endswith("sha-${{ env.SHORT_SHA }}")
    # ...and SHORT_SHA is derived from the commit, not hardcoded.
    assert any("GITHUB_SHA::7" in s.get("run", "") for s in job["steps"])


def test_oci_source_label(job):
    w = _step(job, "docker/build-push-action@")["with"]
    assert f"org.opencontainers.image.source=https://github.com/{FORK_REPO}" in w["labels"]


def test_no_dockerhub_discord_or_extra_secrets():
    text = _text("fork-publish.yml")
    lowered = text.lower()
    assert "dockerhub" not in lowered
    assert "discord" not in lowered
    assert "boulderbadgedad" not in lowered
    import re
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}


@pytest.mark.parametrize("name", ["docker-publish.yml", "dev-nightly.yml"])
def test_upstream_workflows_keep_nezreka_guard(name):
    jobs = _load(name)["jobs"]
    assert jobs and all(j["if"] == "github.repository == 'Nezreka/SoulSync'" for j in jobs.values())
