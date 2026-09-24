# Changelog

Fork-only (kmbrimble/soulsync-multiuser); upstream has none.

## [Unreleased]

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
