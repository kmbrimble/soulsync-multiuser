# Changelog

Fork-only (kmbrimble/soulsync-multiuser); upstream has none.

## [Unreleased]

### 2026-09-25 — Fork CI that goes green on a clean change
- New `.github/workflows/fork-ci.yml`: same Python job as upstream; webui job runs `oxfmt --check`
  and `oxlint --type-check` only on `webui/src` files changed vs `origin/main`, then full build + tests.
  Upstream's `build-and-test.yml` (whole-tree `npm run check`, which fails on upstream debt) is left
  untouched and disabled via `gh workflow disable 365957448`. Spec: `tests/test_fork_ci_workflow.py`.

### 2026-09-25 — Signal-name autocomplete suggests every profile's signals
- `collect_known_signals` now reads `get_all_automations()` (as the engine does since 3e01a165), so
  the builder autocompletes signal names from all profiles, not just Admin + system. Only caller is
  `GET /api/automations/blocks`; still deduped and sorted.

### 2026-09-25 — Paste a Deezer Loved-tracks link on the Sync page
- The Deezer link box (and the Add-playlist sheet's auto-routing) now accepts
  `deezer.com/[locale/]profile/<id>/loved` and `deezer.com/[locale/]library/loved`. New
  `GET /api/deezer/resolve-loved?url=` (`DeezerClient.parse_loved_url`) turns the link into a
  playlist: **own** account (`library/loved`, or `profile/<your id>/loved`) → the profile's
  `LOVEDTRACKS_ID`, loaded through the same ARL path as the "My Deezer playlists" Loved card
  (`/api/deezer/arl-playlist/<id>`, engine id `deezer_arl_<id>`) and synced immediately;
  **another user's public profile** → the public API's `is_loved_track` playlist, loaded as an
  ordinary link playlist. A private profile gets a clear error; a non-admin with no ARL of their
  own is told to connect one under My Accounts (never handed the global ARL owner's list).
- Own Loved is deliberately *not* put in the link-tab discovery engine: its state store is shared
  across profiles and its start refetches via the public API, which refuses Loved.
- Unverified: `/library/loved` is accepted but could not be confirmed against the live site (the
  web app is an SPA that answers 200 for any path).

### 2026-09-24 — Deezer Loved tracks
- **Bug fix (upstream bug):** "My Deezer playlists" was empty for any *private* Deezer profile —
  `api.deezer.com/user/<id>/playlists` and `/playlist/<id>` reject the ARL cookie
  ("This user's profile is private"); only the gw-light API honours it. `get_user_playlists()`
  now lists via gateway `deezer.pageProfile` (public API as fallback) and adds the Loved list from
  `LOVEDTRACKS_ID`; `get_playlist_tracks()` falls back to gateway `playlist.getSongs` when the
  public API refuses (private playlists and Loved), reshaped so release dates / track numbers
  work as before. Public profiles keep using the public path for tracks.
- "My Deezer playlists" lists the profile's Loved (Favourite) tracks first, labelled
  "Loved tracks", and it syncs like any other playlist through the profile's own ARL. No new sync
  path, no virtual id, no OAuth `get_saved_tracks()` (nothing calls it).
- Not done: pasting a `deezer.com/.../loved` URL. The Sync page rejects non-`/playlist/` Deezer
  URLs client-side and the `/api/deezer/playlist/<id>` path can't carry a slashed URL, so it
  needs a webui change.

### 2026-09-24 — Fix: Deezer playlist export wrote into the global ARL owner's account
- Exporting a mirrored playlist to Deezer always logged in with the global ARL, so profile K/T's
  export landed in the ARL owner's account. The export worker now receives the requesting
  profile and uses that profile's own Deezer client. A non-admin profile with no ARL of its own
  is refused ("Connect your Deezer account under My Accounts…") rather than writing into another
  person's account. Admin (profile 1) is unchanged. `create_or_update_playlist` is the only
  Deezer write path in the codebase.

### 2026-09-24 — Per-profile Deezer ARL
- Each non-admin profile can set its own Deezer ARL (My Accounts → Deezer). The Sync page's
  "My Deezer playlists" and Discover "Your Artists/Albums" then use *that* account instead of the
  global ARL owner's. Admin and profiles without an ARL behave exactly as before (global client).
- Storage: new nullable column, added by an idempotent migration (`_add_profile_deezer_arl`):
  `ALTER TABLE profiles ADD COLUMN deezer_arl TEXT DEFAULT NULL` — fernet-encrypted like the
  Navidrome password. New `core/profile_deezer.py` resolver (cached per profile, rebuilt when the
  ARL changes); `DeezerDownloadClient(arl=...)` builds a dedicated client without touching global
  config or the global session. If a profile's row can't be read the resolver returns no client
  rather than falling back to the global one.
- API: `GET/POST/DELETE /api/profiles/me/deezer-arl` (POST live-logs-in before saving; GET never
  returns the ARL; admin is refused — managed in Settings). `/api/profiles/me/connections` gains `deezer`.
- Async playlist loads capture the requesting profile; job dedupe is per profile and another
  profile's job id returns 404 from `/api/deezer/playlist-load/<id>`.
- Switched to the resolver: `/api/deezer/arl-status`, `arl-playlists`, `arl-playlist/<id>` (sync
  and async job), Discover your-artists/your-albums fetch and their `/sources` connected checks.
- Left on the GLOBAL ARL (explicit non-goal, shared library): audio downloads via the `deezer_dl`
  source, `/api/deezer-download/test*`, Deezer playlist export.
- Tests: `tests/test_profile_deezer_arl.py`.

### 2026-09-24 — Fix: non-admin profiles' automations never scheduled
- Added `MusicDatabase.get_all_automations()`; `get_automations()` is untouched (a `None`
  profile sentinel would have leaked all profiles to unauthenticated requests).
- `run_automation` already ran under the owner's profile, but the event/signal path
  (`_run_event_automation`) did not — it now wraps the handler in `set_background_profile(owner)`.
- Changed to all-profiles: `AutomationEngine.start()`, `_rebuild_event_cache()` (scheduling and
  event/signal triggers), `core/automation/api.py::_check_create_cycle` and `_check_update_cycle`
  (signals cross profiles in the engine, so cycle detection must see them all), `core/debug_info.py`
  (automation count).
- Left as-is: `_purge_orphaned_system_automations` (system rows only), `collect_known_signals`
  (unchanged upstream behaviour: builder autocomplete lists Admin+system signals only — cosmetic,
  not correctness), `list_automations` (explicit per-profile `profile_id`).
- Behaviour note: `is_event_action_enabled` now counts any profile's enabled automation, consistent
  with the event cache. Cycle-check messages may name other profiles' automations.
- Tests: `tests/automation/test_all_profiles_scheduling.py`.

### 2026-09-24 — Fork image publishing to GHCR
- Added `.github/workflows/fork-publish.yml`: on push to `main` / manual dispatch, builds the
  `Dockerfile` (linux/amd64 only, `COMMIT_SHA` build-arg as upstream) and pushes
  `ghcr.io/kmbrimble/soulsync-multiuser:latest` and `:sha-<short sha>` using only `GITHUB_TOKEN`.
  Guarded to the fork repo; upstream's publish workflows are untouched.
- Note: `.gitignore:45` (`**/.*/`) ignores new files under `.github/`; `fork-publish.yml` had to be
  `git add -f`'d. Any future fork workflow needs the same.
- Added `tests/test_fork_publish_workflow.py` (workflow spec + upstream-guard regression) and
  `pyyaml` to `requirements-dev.txt` for it.
