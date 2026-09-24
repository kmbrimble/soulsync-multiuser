# CLAUDE.md — soulsync-multiuser (fork of Nezreka/SoulSync)

This is Kieren's fork of `Nezreka/SoulSync` (MIT). Upstream owns ~99% of this code; the fork adds
a small number of multi-user features. Keep fork changes **small, isolated and easy to rebase**
onto upstream — new code in new functions/files where practical, minimal edits to upstream files.

## Why this fork exists

Two SoulSync profiles, K (profile 2) and T (profile 3), each need their **own Deezer account**
syncing privately to their **own Navidrome user**, plus Deezer "Loved/Favourites" support.
Per-profile Navidrome login already works upstream (`navidrome_client_for_profile` in
`services/sync_service.py`) — do not re-touch it unless it regresses.

Planned fork work, each a separate `/feature` run:
1. ~~GHCR image build~~ **Done 24 Sep 2026** — `.github/workflows/fork-publish.yml`, image public at
   `ghcr.io/kmbrimble/soulsync-multiuser`.
2. Automation scheduler bug: `MusicDatabase.get_automations(profile_id=1)` default means
   no-arg callers only see Admin + system automations, so K/T automations never self-schedule.
3. Per-profile Deezer ARL: `deezer_client_for_profile()` modelled on
   `get_tidal_client_for_profile()` (`web_server.py`) — **ARL stored on the profile row, the
   same way per-profile Tidal tokens are** (decided 24 Sep 2026), not in the Phase-0
   `service_credentials` tables. The main target is the ARL-backed `deezer_dl` client
   (`core/deezer_download_client.py`), which today serves *every* profile the ARL owner's
   playlists (private included) via `/api/deezer/arl-*` in `api/source_playlists.py`, plus
   Discover favourites in `api/discover_routes.py`. Every `download_orchestrator.client("deezer_dl")`
   use in a profile-scoped request needs to resolve per profile. Includes a UI to set it.
4. Deezer Loved/Favourites: real `get_saved_tracks()` / `get_saved_tracks_count()` in
   `core/deezer_client.py` plus a virtual playlist id, modelled on `tidal-favorites` /
   `QOBUZ_FAVORITES_ID`.

## Test and lint (mirror `.github/workflows/build-and-test.yml`)

Python venv lives at `.venv` in the shared checkout (Python 3.11, created with uv). In a
worktree, create your own: `uv venv -q -p 3.11 .venv && uv pip install -q -r requirements-dev.txt`.

- `python -m ruff check .`
- `python -m compileall -q api core database services scripts web_server.py wsgi.py beatport_unified_scraper.py`
- `PYTHONPATH=$PWD python -m pytest -q` (live SoundCloud/YouTube tests are excluded by default
  via `pyproject.toml`; never enable them)
- WebUI, only if `webui/` changed: `cd webui && npm ci && npm run check && npm run build && npm test`

**Runtime:** the full local pytest suite is ~19.5k tests and took 36 min on this container
(baseline at `ae77a6e`, 24 Sep 2026: 19560 passed, 9 skipped). During the Step 6 fix loop run the
targeted test files; run the full suite once before pushing, or let the feature-branch CI run
(~21 min for the Python job) be the full-suite gate. Never run two full suites at once
(`pgrep -f "^python -m pytest"`).

**Known baseline CI failure:** the `webui` job fails at `npm run check` on upstream code
("Format issues found in … 102 files" at `ae77a6e`). It is pre-existing upstream formatting debt —
not a blocker, and do not mass-reformat `webui/` (it would conflict with every upstream merge).
Because that step fails first, CI never reaches `npm run build` / `npm test`: if a change touches
`webui/`, run build + tests locally and only check formatting on the files you changed.

Tests use pytest in `tests/`; match existing style. Tests must never hit the real Deezer,
Navidrome, Tidal or any network service — mock at the client boundary.

## Non-negotiable constraints

- **No credentials anywhere in the repo, tests, fixtures, logs or commit messages.** ARLs,
  passwords, tokens are fake placeholders in tests. Pre-push secrets review is mandatory; this
  work involves two real people's Deezer credentials.
- **Never push to upstream.** The `upstream` remote's push URL is deliberately disabled. No PRs
  to `Nezreka/SoulSync`.
- **Never touch the live deployment** (SoulSync/Navidrome containers on the homelab host, their
  DBs, templates or SecretsMan). The agent has no deploy step to live;
  deploying is Kieren's manual action.
- **Admin (profile 1) and unconfigured profiles must behave exactly as upstream does** — the
  global client/credentials stay the fallback. A profile with no per-profile setting must be
  indistinguishable from upstream behaviour.
- Upstream's `docker-publish.yml`, `dev-nightly.yml`, `cleanup-dev-images.yml` are guarded by
  `github.repository == 'Nezreka/SoulSync'` — leave them alone so merges from upstream stay
  clean. Fork CI/publishing goes in separate, fork-named workflow files.
- Any profile-scoped API call made in tests or scripts must set the profile explicitly — an
  unset session falls back to profile 1 (`core/profile_context.py`), which silently breaks
  per-profile attribution.

## Repo gotchas

- **`gh` resolves to the upstream parent by default in a fork.** `gh repo set-default
  kmbrimble/soulsync-multiuser` has been run in the shared checkout, but always pass
  `-R kmbrimble/soulsync-multiuser` to `gh run …` / `gh pr …` anyway (worktrees may not inherit it).
- **`.gitignore` line `**/.*/` ignores new files under `.github/`.** A new workflow file must be
  `git add -f`'d, and checked with `git ls-files .github/workflows`.
- **Never background a wait** (`run_in_background`, `&`) in a connector-started session — the
  headless turn ends and the watch is orphaned. Run `gh run watch … --exit-status` in the
  foreground with a long timeout.

## Deploy and verify

`.github/workflows/build-and-test.yml` runs on pushes to any branch **except** `main`/`dev`.
So CI must be proven on the feature branch *before* it reaches main:

1. `git push -u origin HEAD` (feature branch), then `gh run list --branch <branch> -L 1` and
   `gh run watch <id> --exit-status`. Red CI → report and stop, do not merge.
2. Green → `git push origin HEAD:main` (fast-forward only, never force).
3. Watch `.github/workflows/fork-publish.yml` ("Fork - Build and Push Image (GHCR)") on `main`
   to green. It publishes `ghcr.io/kmbrimble/soulsync-multiuser:latest` and `:sha-<short>`.
   Upstream's "Build and Push Docker Image" also queues on main and skips via its repo guard —
   that's expected.
4. Hand back. Kieren deploys manually (unRAID template / force update). Never do it yourself.

## Changelog

`CHANGELOG.md` is fork-only (upstream has none). Keep entries under `## [Unreleased]`; no
version-numbering convention is declared yet, so do not invent version numbers.

## Syncing upstream

`git fetch upstream && git merge upstream/main` on a branch, full test run, then fast-forward
main. Expect conflicts only where fork edits touched upstream files — another reason to keep
those edits minimal.
