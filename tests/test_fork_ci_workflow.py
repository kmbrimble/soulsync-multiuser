"""Spec for the fork-only CI workflow that replaces upstream's build-and-test.yml on the fork."""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
FORK_REPO = "kmbrimble/soulsync-multiuser"


@pytest.fixture(scope="module")
def wf():
    return yaml.safe_load((WORKFLOWS / "fork-ci.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def upstream():
    return yaml.safe_load((WORKFLOWS / "build-and-test.yml").read_text(encoding="utf-8"))


def _runs(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


def _step_running(job, needle):
    matches = [s for s in job["steps"] if needle in s.get("run", "")]
    assert matches, needle
    return matches[0]


def test_triggers_match_upstream_push_all_but_main_dev(wf, upstream):
    # PyYAML parses the bare key `on` as boolean True.
    triggers = wf.get("on", wf.get(True))
    assert triggers == upstream.get("on", upstream.get(True))
    assert triggers["push"]["branches-ignore"] == ["main", "dev"]


def test_jobs_present_and_guarded_to_fork(wf):
    assert {"sanity-check", "webui"} <= set(wf["jobs"])
    for job in wf["jobs"].values():
        assert job["if"] == f"github.repository == '{FORK_REPO}'"
        assert isinstance(job["timeout-minutes"], int) and job["timeout-minutes"] > 0


def test_least_privilege_and_per_branch_cancelling_concurrency(wf):
    assert wf["permissions"] == {"contents": "read"}
    conc = wf["concurrency"]
    assert "github.ref" in conc["group"]
    assert conc["cancel-in-progress"] is True


# Upstream pytest tests that are flaky on GitHub runners. This list must only ever shrink.
EXPECTED_PYTEST_DESELECTS = [
    # purge_old(days=1e-7) is an 8.6 ms window; races on fast runners (fork-ci run 36092811156)
    "tests/test_audiobook_recycle.py::test_purging_forgets_the_entry_it_removed",
]


def _strip_deselects(run):
    if not run:
        return run
    tokens = run.split()
    kept = [t for i, t in enumerate(tokens) if t != "--deselect" and (i == 0 or tokens[i - 1] != "--deselect")]
    return " ".join(kept)


def test_python_job_mirrors_upstream(wf, upstream):
    ours, theirs = wf["jobs"]["sanity-check"], upstream["jobs"]["sanity-check"]
    assert [_strip_deselects(s.get("run")) for s in ours["steps"]] == \
        [_strip_deselects(s.get("run")) for s in theirs["steps"]]
    assert "python -m pytest" in _runs(ours)


def test_pytest_deselects_are_exactly_the_known_flaky_tests(wf):
    step = _step_running(wf["jobs"]["sanity-check"], "python -m pytest")
    tokens = step["run"].split()
    deselects = [tokens[i + 1] for i, t in enumerate(tokens) if t == "--deselect"]
    assert deselects == EXPECTED_PYTEST_DESELECTS


def test_webui_installs_builds_and_tests(wf):
    runs = _runs(wf["jobs"]["webui"])
    for cmd in ("npm ci", "npm run build", "npm test"):
        assert cmd in runs


def test_webui_checks_are_scoped_to_changed_files_not_whole_tree(wf):
    job = wf["jobs"]["webui"]
    runs = _runs(job)
    # Not upstream's whole-tree `npm run check` (baseline format + lint debt).
    assert "npm run check" not in runs
    assert "oxfmt --check src" not in runs and "oxlint --type-check src" not in runs
    # Needs history for the merge-base, and diffs against origin/main.
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    step = _step_running(job, "oxfmt --check")
    assert "git diff" in step["run"] and "origin/main" in step["run"]
    assert "oxlint" in step["run"] and "--type-check" in step["run"]
    # Skips cleanly when nothing changed.
    assert "exit 0" in step["run"] or "-z" in step["run"]


def test_no_step_swallows_failures(wf):
    for job in wf["jobs"].values():
        assert "continue-on-error" not in job
        for s in job["steps"]:
            assert "continue-on-error" not in s, s
            assert "|| true" not in s.get("run", ""), s


# Pre-existing upstream failures at bfa2921. This list must only ever shrink.
EXPECTED_TEST_EXCLUDES = [
    "src/platform/artwork-thumb.wiring.test.ts",
    "src/test/chat-overlay-share-fns.test.ts",
    "src/test/chat-plain-mode.test.ts",
    "src/test/export-coverage.test.ts",
    "src/routes/podcasts/-route.test.tsx",
    "src/routes/import/-route.test.tsx",  # fails on GitHub runners only (2/2 CI runs), passes locally
]


def test_webui_test_exclusions_are_exactly_the_known_baseline_failures(wf):
    step = next(s for s in wf["jobs"]["webui"]["steps"] if "npm test" in s.get("run", ""))
    tokens = step["run"].split()
    excludes = [tokens[i + 1] for i, t in enumerate(tokens) if t == "--exclude"]
    assert excludes == EXPECTED_TEST_EXCLUDES


def test_vitest_retry_is_bounded_at_two_and_pytest_has_none(wf):
    import re

    step = next(s for s in wf["jobs"]["webui"]["steps"] if "npm test" in s.get("run", ""))
    assert re.findall(r"--retry[= ](\d+)", step["run"]) == ["2"]
    py = "\n".join(s.get("run", "") for s in wf["jobs"]["sanity-check"]["steps"])
    assert "retry" not in py.lower() and "reruns" not in py.lower()
