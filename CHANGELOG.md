# Changelog

Fork-only (kmbrimble/soulsync-multiuser); upstream has none.

## [Unreleased]

### 2026-09-24 — Fork image publishing to GHCR
- Added `.github/workflows/fork-publish.yml`: on push to `main` / manual dispatch, builds the
  `Dockerfile` (linux/amd64 only, `COMMIT_SHA` build-arg as upstream) and pushes
  `ghcr.io/kmbrimble/soulsync-multiuser:latest` and `:sha-<short sha>` using only `GITHUB_TOKEN`.
  Guarded to the fork repo; upstream's publish workflows are untouched.
- Added `tests/test_fork_publish_workflow.py` (workflow spec + upstream-guard regression) and
  `pyyaml` to `requirements-dev.txt` for it.
