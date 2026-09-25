"""Fork: wishlist downloads keep their owning profile end to end, so a profile
with its own library folder (K/T) gets its downloads there, not in the shared
transfer folder."""

from types import SimpleNamespace

import pytest

from core.wishlist import resolution
from core.wishlist.processing import process_wishlist_automatically
from core.wishlist.selection import filter_wishlist_tracks_by_category, sanitize_and_dedupe_wishlist_tracks
from core.wishlist.service import WishlistService
from tests.wishlist.test_automation import _build_runtime


@pytest.fixture(autouse=True)
def _folders(monkeypatch):
    """profiles 2 and 3 have their own folder; 4 and 5 (and admin 1) do not."""
    from core.imports import paths
    monkeypatch.setattr(paths, "library_root_for_profile",
                        lambda pid: f"/music/P{pid}" if pid in (2, 3) else None)


def _track(tid, profile_id=None):
    t = {"id": tid, "name": f"Song {tid}", "artists": [{"name": "A"}], "album": {"name": "Alb"}}
    if profile_id is not None:
        t["profile_id"] = profile_id
    return t


# ---- dedupe must not merge the same track across profiles -------------------

def test_sanitize_keeps_same_track_for_two_profiles():
    tracks, dupes = sanitize_and_dedupe_wishlist_tracks([_track("x", 2), _track("x", 3)])
    assert dupes == 0
    assert [t["profile_id"] for t in tracks] == [2, 3]


def test_sanitize_still_merges_profiles_without_a_folder():
    """admin + folderless profiles share one library: one download, as upstream."""
    tracks, dupes = sanitize_and_dedupe_wishlist_tracks([_track("x", 1), _track("x", 4), _track("x", 5)])
    assert dupes == 2
    assert [t["profile_id"] for t in tracks] == [1]


def test_category_filter_still_merges_profiles_without_a_folder():
    filtered, _ = filter_wishlist_tracks_by_category(
        [_track("x", 1), _track("x", 4)], "albums", classifier=lambda t: "albums")
    assert len(filtered) == 1


def test_sanitize_still_dedupes_within_one_profile_and_unstamped():
    tracks, dupes = sanitize_and_dedupe_wishlist_tracks(
        [_track("x", 2), _track("x", 2), _track("y"), _track("y")])
    assert dupes == 2
    assert len(tracks) == 2


def test_category_filter_keeps_same_track_for_two_profiles():
    tracks = [_track("x", 2), _track("x", 3), _track("x", 3)]
    filtered, _ = filter_wishlist_tracks_by_category(tracks, "albums", classifier=lambda t: "albums")
    assert [t["profile_id"] for t in filtered] == [2, 3]


# ---- the service stamps the owner --------------------------------------------

def test_service_stamps_profile_id_on_download_tracks():
    svc = WishlistService(database_path="test.db")
    svc._database = SimpleNamespace(get_wishlist_tracks=lambda limit=None, profile_id=1: [{
        "id": 5, "spotify_track_id": "sp1", "track_data": {"name": "N", "artists": [{"name": "A"}]},
        "failure_reason": None, "retry_count": 0, "date_added": None, "last_attempted": None,
        "source_type": "x", "source_info": None,
    }])
    out = svc.get_wishlist_tracks_for_download(profile_id=2)
    assert out[0]["profile_id"] == 2


# ---- auto batch is split per owning profile ----------------------------------

def test_auto_wishlist_makes_one_batch_per_profile_carrying_profile_id():
    batch_map = {}
    runtime, service, *_rest, executor, _logger, _progress, _events = _build_runtime(
        tracks=[], count=2, batch_map=batch_map, profiles=[{"id": 1}, {"id": 2}, {"id": 3}])
    per_profile = {1: [_track("a")], 2: [_track("x"), _track("a")], 3: [_track("x")]}
    service.get_wishlist_tracks_for_download = lambda profile_id=1: [dict(t) for t in per_profile[profile_id]]
    service.get_wishlist_count = lambda profile_id=1: len(per_profile[profile_id])

    process_wishlist_automatically(runtime, automation_id="auto-x")

    batches_by_profile = {}
    for bid, b in batch_map.items():
        batches_by_profile.setdefault(b["profile_id"], []).append(bid)
    assert sorted(batches_by_profile) == [1, 2, 3]
    submitted = {}
    for _fn, args, _kw in executor.submissions:
        for t in args[2]:
            submitted.setdefault(args[0], []).append(t)
    for pid, bids in batches_by_profile.items():
        for bid in bids:
            assert {t["profile_id"] for t in submitted[bid]} == {pid}
    # profile 2 keeps both its tracks, profile 3 keeps its copy of the shared id
    assert sum(len(submitted[b]) for b in batches_by_profile[2]) == 2
    assert sum(len(submitted[b]) for b in batches_by_profile[3]) == 1


# ---- completion marks the owner's wishlist row -------------------------------

def _svc():
    calls = []
    return SimpleNamespace(
        mark_track_download_result=lambda tid, success, error_message=None, profile_id=1:
            calls.append((tid, profile_id)) or True,
        get_wishlist_tracks_for_download=lambda profile_id=1: [],
    ), calls


def test_check_and_remove_marks_owning_profile():
    svc, calls = _svc()
    ctx = {"track_info": {"id": "sp1", "profile_id": 2, "name": "n"}, "search_result": {}, "original_search_result": {}}
    resolution.check_and_remove_from_wishlist(
        ctx, wishlist_service=svc, database=SimpleNamespace(get_all_profiles=lambda: [{"id": 1}]))
    assert calls == [("sp1", 2)]


def test_remove_by_metadata_marks_owning_profile():
    svc, calls = _svc()
    assert resolution.check_and_remove_track_from_wishlist_by_metadata(
        {"id": "sp1", "profile_id": 3, "name": "n"}, wishlist_service=svc)
    assert calls == [("sp1", 3)]


def test_check_and_remove_without_profile_defaults_to_one():
    svc, calls = _svc()
    ctx = {"track_info": {"id": "sp1", "name": "n"}, "search_result": {}, "original_search_result": {}}
    resolution.check_and_remove_from_wishlist(
        ctx, wishlist_service=svc, database=SimpleNamespace(get_all_profiles=lambda: [{"id": 1}]))
    assert calls == [("sp1", 1)]
