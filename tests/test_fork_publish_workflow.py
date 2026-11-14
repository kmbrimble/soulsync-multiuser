"""Spec for the fork-only Gitea registry image workflow and the upstream guards it leaves alone.

The image is built by .gitea/workflows/image.yml (Gitea Actions) and pushed to Gitea's container registry. The old GHCR
workflow (.github/workflows/fork-publish.yml) is gone; upstream's own GitHub workflows stay untouched and stay guarded
to Nezreka/SoulSync so they never run here.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
GITEA = ROOT / ".gitea" / "workflows"
WORKFLOWS = ROOT / ".github" / "workflows"
IMAGE = "git.kiztigs.com/kmbrimble/soulsync-multiuser"


def _text():
    return (GITEA / "image.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load(_text())


@pytest.fixture(scope="module")
def job(wf):
    assert len(wf["jobs"]) == 1
    return next(iter(wf["jobs"].values()))


def test_ghcr_fork_workflow_is_gone():
    assert not (WORKFLOWS / "fork-publish.yml").exists()
    assert "ghcr.io" not in _text()


def test_triggers_are_push_main_and_dispatch(wf):
    # PyYAML parses the bare key `on` as boolean True.
    triggers = wf.get("on", wf.get(True))
    assert triggers["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in triggers
    assert set(triggers) == {"push", "workflow_dispatch"}


def test_timeout_and_serialised_concurrency(wf, job):
    assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] > 0
    assert wf["concurrency"]["group"]
    assert wf["concurrency"]["cancel-in-progress"] is False


def test_checkout_is_sha_pinned_with_a_version_comment(job):
    uses = [s["uses"] for s in job["steps"] if "uses" in s]
    assert uses and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in uses)
    assert re.search(r"uses:\s*actions/checkout@[0-9a-f]{40}\s+#\s*v\d", _text())


def test_login_is_the_gitea_registry_with_the_org_secret_only(wf):
    text = _text()
    assert wf["jobs"]["image"]["env"]["REGISTRY"] == "git.kiztigs.com"
    assert re.search(r"docker login \"\$REGISTRY\" -u butler-bot --password-stdin", text)
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"REGISTRY_TOKEN"}


def test_build_is_amd64_only_with_commit_sha_and_registry_cache():
    text = _text()
    assert "--platform linux/amd64" in text
    assert '--build-arg COMMIT_SHA="$(git rev-parse HEAD)"' in text
    assert "--push" in text
    assert 'type=registry,ref=$IMAGE:buildcache' in text
    assert "mode=max" in text


def test_tags_are_latest_on_main_and_short_sha(wf):
    text = _text()
    assert wf["jobs"]["image"]["env"]["IMAGE"] == IMAGE
    assert '-t $IMAGE:sha-$SHORT' in text
    assert 'if [ "$BUILD_REF" = main ]; then TAGS="$TAGS -t $IMAGE:latest"' in text
    assert "git rev-parse --short=7 HEAD" in text


def test_oci_source_label():
    assert f"org.opencontainers.image.source=https://{IMAGE.split('/')[0]}/kmbrimble/soulsync-multiuser" in _text()


def test_no_dockerhub_discord_or_foreign_secrets():
    lowered = _text().lower()
    for word in ("dockerhub", "discord", "boulderbadgedad"):
        assert word not in lowered


@pytest.mark.parametrize("name", ["docker-publish.yml", "dev-nightly.yml"])
def test_upstream_workflows_keep_nezreka_guard(name):
    jobs = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]
    assert jobs and all(j["if"] == "github.repository == 'Nezreka/SoulSync'" for j in jobs.values())
