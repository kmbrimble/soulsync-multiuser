# CLAUDE.md — soulsync-multiuser (fork of Nezreka/SoulSync)

This is Kieren's fork of `Nezreka/SoulSync` (MIT). Upstream owns ~99% of this code; the fork adds
a small number of multi-user features. Keep fork changes **small, isolated and easy to rebase**
onto upstream — new code in new functions/files where practical, minimal edits to upstream files.

## Why this fork exists

Two SoulSync profiles, K (profile 2) and T (profile 3), each need their **own Deezer account**
syncing privately to their **own Navidrome user**, plus Deezer "Loved/Favourites" support.
Per-profile Navidrome login already works upstream (`navidrome_client_for_profile` in
`services/sync_service.py`) — do not re-touch it unless it regresses.

Fork work (each a separate `/feature` run) — all shipped and deployed 24–25 Sep 2026:
1. GHCR image publishing — `.github/workflows/fork-publish.yml` → `ghcr.io/kmbrimble/soulsync-multiuser`.
2. Automation scheduler: `MusicDatabase.get_all_automations()` used by engine start / event cache /
   cycle checks / debug info; event automations run under their owner's background profile.
3. Per-profile Deezer ARL: `profiles.deezer_arl` (Fernet), `core/profile_deezer.py::resolve_deezer_dl_client`
   (admin/unconfigured → global; own ARL → dedicated cached client; unreadable row → None),
   `GET/POST/DELETE /api/profiles/me/deezer-arl`, Deezer row in My Accounts. Audio downloads stay global.
3b. Deezer playlist export uses the requesting profile's ARL; a non-admin without one is refused
   (never writes into the global ARL owner's account).
4. Private profiles + Loved tracks: `get_user_playlists()` lists via gw-light `deezer.pageProfile`
   and adds Loved from `LOVEDTRACKS_ID`; `get_playlist_tracks()` falls back to gw `playlist.getSongs`.
   The public api.deezer.com refuses private profiles even with the ARL cookie — use gw-light for
   anything account-private.

Known follow-ups (not done): pasting a `deezer.com/…/loved` URL on the Sync page (needs webui);
`collect_known_signals` autocomplete is still admin+system only.

## Test and lint (mirror `.github/workflows/build-and-test.yml`)

Python venv lives at `.venv` in the shared checkout (Python 3.11, created with uv). In a
worktree, create your own: `uv venv -q -p 3.11 .venv && uv pip install -q -r requirements-dev.txt`.

- `python -m ruff check .`
- `python -m compileall -q api core database services scripts web_server.py wsgi.py beatport_unified_scraper.py`
- `PYTHONPATH=$PWD python -m pytest -q` (live SoundCloud/YouTube tests are excluded by default
  via `pyproject.toml`; never enable them)
- WebUI, only if `webui/` changed: `cd webui && npm ci && npm run build && npm test`, plus
  `npx oxfmt --check <files> && npx oxlint --type-check <files>` on the `webui/src` files you changed
  (not `npm run check` — whole-tree, fails on upstream debt; see fork-ci below)

**Runtime:** the full local pytest suite is ~19.5k tests and took 36 min on this container
(baseline at `ae77a6e`, 24 Sep 2026: 19560 passed, 9 skipped). During the Step 6 fix loop run the
targeted test files; run the full suite once before pushing, or let the feature-branch CI run
(~21 min for the Python job) be the full-suite gate. Never run two full suites at once
(`pgrep -f "^python -m pytest"`).

**CI is `.github/workflows/fork-ci.yml`, not upstream's `build-and-test.yml`.** Upstream's webui job
runs `npm run check` over the whole tree, which fails on pre-existing upstream debt (format issues in
~102 files, 16 oxlint/type errors at `bfa2921`). Upstream's workflow is therefore disabled in the fork
via `gh workflow disable 365957448 -R kmbrimble/soulsync-multiuser` (a repo setting, so the file stays
untouched for clean merges; re-enable with `gh workflow enable 365957448`). fork-ci runs the same Python
job, and in the webui job runs `oxfmt --check` + `oxlint --type-check` only on `webui/src` files changed
vs `origin/main` (skipped if none), then the full `npm run build` and `npm test`. Do not mass-reformat
`webui/` (it would conflict with every upstream merge). vitest can time out under heavy local load;
CI is the reference.

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
- **Connector sessions can't `cd` into `/projects/.worktrees/…`.** Use absolute paths instead:
  `git -C <wt>`, `PYTHONPATH=<wt> /projects/soulsync-multiuser/.venv/bin/pytest -p no:cacheprovider
  --rootdir=<wt> <wt>/tests/<file>`, `npm --prefix <wt>/webui …`.
- **A connector turn is capped at 60 min** and CI takes 21–40 min: stop cleanly around 50 min
  and state where you are rather than being killed mid-watch.
- **Tests that spawn bare threads** see a different `get_database()` (thread-local, reads
  `DATABASE_PATH`, which web_server test modules overwrite at import) — pin it in the test.
- **Never background a wait** (`run_in_background`, `&`) in a connector-started session — the
  headless turn ends and the watch is orphaned. Run `gh run watch … --exit-status` in the
  foreground with a long timeout.

## Deploy and verify

`.github/workflows/fork-ci.yml` ("Fork - Compile the app and run tests") runs on pushes to any branch
**except** `main`/`dev`. So CI must be proven on the feature branch *before* it reaches main:

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
those edits minimal. On a sync branch fork-ci's webui format/lint step diffs against `origin/main`, so
it also checks every `webui/src` file upstream touched and can go red on upstream's own debt — expected;
judge it by the build/test steps and the Python job.
