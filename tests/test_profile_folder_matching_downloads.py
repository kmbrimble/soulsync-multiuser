"""Per-profile music folder (fork): a profile with ``library_path_prefix`` matches
playlist tracks only against its own folder and has its downloads organised
under ``<share root>/<prefix>``. Admin / unset profiles are unchanged.

Real temp DB and temp dirs; no network.
"""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.profile_context import get_current_profile_id, reset_background_profile, set_background_profile
from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    # (track id, artist, title, real path in the share or None = never mapped)
    rows = [
        ("1", "ArtK", "Kilo Anthem", "KMusic/ArtK/A/1.flac"),
        ("2", "ArtT", "Tango Ballad", "TMusic/ArtT/A/1.flac"),
        ("3", "ArtS", "Shared Song", "SoulSync/organized/ArtS/A/1.flac"),
        ("4", "ArtU", "Unmapped Song", None),
        ("5", "ArtD", "Dup Song", "KMusic/ArtD/A/1.flac"),
        ("6", "ArtD", "Dup Song", "TMusic/ArtD/A/1.flac"),
    ]
    for i, (tid, artist, title, path) in enumerate(rows, start=1):
        c.execute("INSERT OR IGNORE INTO artists (id, name, server_source) VALUES (?, ?, 'navidrome')",
                  (artist, artist))
        c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'navidrome')",
                  (f"al{i}", artist, f"Album {i}"))
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) "
                  "VALUES (?, ?, ?, ?, 'navidrome', ?)", (tid, f"al{i}", artist, title, f"Fake/Fake/{tid}.flac"))
        if path:
            c.execute("INSERT INTO track_library_folder (track_id, rel_path) VALUES (?, ?)", (tid, path))
    c.commit()
    c.close()
    # the code under test asks get_database() for the profile row
    import database.music_database as md
    monkeypatch.setattr(md, "get_database", lambda: d)
    return d


class _As:
    def __init__(self, pid):
        self.pid = pid

    def __enter__(self):
        self.tok = set_background_profile(self.pid)

    def __exit__(self, *a):
        reset_background_profile(self.tok)


def _profile(db, name, prefix=None):
    pid = db.create_profile(name=name)
    if prefix is not None:
        assert db.set_profile_library_prefix(pid, prefix)
    return pid


def _hit(db, pid, title, artist):
    with _As(pid):
        track, _conf = db.check_track_exists(title, artist, confidence_threshold=0.7, server_source="navidrome")
    return track.id if track else None


# ── matching ─────────────────────────────────────────────────────────────────

def test_k_matches_only_its_own_folder(db):
    k = _profile(db, "k", "KMusic")
    assert _hit(db, k, "Kilo Anthem", "ArtK") == "1"
    assert _hit(db, k, "Tango Ballad", "ArtT") is None          # exists only in TMusic -> missing for K
    assert _hit(db, k, "Shared Song", "ArtS") is None     # SoulSync/ is not K's folder
    assert _hit(db, k, "Unmapped Song", "ArtU") is None   # no folder-map row -> missing


def test_t_matches_only_its_own_folder_and_picks_its_copy(db):
    t = _profile(db, "t", "TMusic")
    assert _hit(db, t, "Tango Ballad", "ArtT") == "2"
    assert _hit(db, t, "Kilo Anthem", "ArtK") is None
    assert _hit(db, t, "Dup Song", "ArtD") == "6"          # not K's copy


def test_k_picks_its_copy_of_a_duplicate(db):
    assert _hit(db, _profile(db, "k", "KMusic"), "Dup Song", "ArtD") == "5"


@pytest.mark.parametrize("who", ["admin", "plain"])
def test_admin_and_unset_profiles_match_everything(db, who):
    pid = 1 if who == "admin" else _profile(db, "plain")
    for title, artist in [("Kilo Anthem", "ArtK"), ("Tango Ballad", "ArtT"), ("Shared Song", "ArtS"),
                          ("Unmapped Song", "ArtU")]:
        assert _hit(db, pid, title, artist) is not None, title


def test_prebuilt_candidate_list_is_scoped_too(db):
    with _As(1):
        cands = [db.get_track_by_id("2"), db.get_track_by_id("1")]
    k = _profile(db, "k", "KMusic")
    with _As(k):
        track, _ = db.check_track_exists("Tango Ballad", "ArtT", confidence_threshold=0.7, candidate_tracks=cands)
        assert track is None
        track, _ = db.check_track_exists("Kilo Anthem", "ArtK", confidence_threshold=0.7, candidate_tracks=cands)
        assert track is not None and track.id == "1"


def test_cached_or_manual_match_outside_the_folder_is_rejected(db):
    k = _profile(db, "k", "KMusic")
    with _As(k):
        assert db.track_in_current_profile_folder("1") is True
        assert db.track_in_current_profile_folder("2") is False
        assert db.track_in_current_profile_folder("4") is False    # unmapped
        assert db.track_in_current_profile_folder(None) is False
    with _As(1):
        assert db.track_in_current_profile_folder("4") is True     # admin: no scoping at all


def test_sync_runs_as_the_playlist_owner(monkeypatch):
    """sync_playlist must make the owner the current profile, or the scoping above
    would read profile 1 inside the sync thread."""
    from services.sync_service import PlaylistSyncService as SyncService
    svc = SyncService.__new__(SyncService)
    seen = []

    async def fake(self_, playlist, download_missing, profile_id, sync_mode):
        seen.append(get_current_profile_id())
        return "done"

    monkeypatch.setattr(SyncService, "_sync_playlist", fake)
    assert asyncio.run(svc.sync_playlist(None, profile_id=3)) == "done"
    assert seen == [3]
    assert get_current_profile_id() == 1          # restored afterwards


# ── download destination ─────────────────────────────────────────────────────

@pytest.fixture()
def share(tmp_path, monkeypatch):
    root = tmp_path / "host" / "music"
    (root / "SoulSync" / "organized").mkdir(parents=True)
    (root / "KMusic").mkdir()
    from core.settings import config_manager
    real_get = config_manager.get
    cfg = {"soulseek.transfer_path": str(root / "SoulSync" / "organized"),
           "library.music_paths": [str(root)]}
    monkeypatch.setattr(config_manager, "get", lambda k, d=None: cfg.get(k, real_get(k, d)))
    return root, cfg


def test_destination_root_per_profile(db, share):
    from core.imports.paths import library_root_for_profile, transfer_root_for_context
    root, _ = share
    k = _profile(db, "k", "KMusic")
    t = _profile(db, "t", "TMusic")          # folder not on disk yet: still <root>/TMusic
    plain = _profile(db, "plain")
    transfer = str(root / "SoulSync" / "organized")
    assert library_root_for_profile(k) == str(root / "KMusic")
    assert library_root_for_profile(t) == str(root / "TMusic")
    assert transfer_root_for_context({"profile_id": k}) == str(root / "KMusic")
    # admin, unset profile, and no profile at all keep the shared transfer folder
    for ctx in ({"profile_id": 1}, {"profile_id": plain}, {}, None):
        assert transfer_root_for_context(ctx) == transfer, ctx
    assert library_root_for_profile(None) is None and library_root_for_profile(1) is None


def test_batch_attribution_reaches_the_profile_root(db, share):
    """wishlist and playlist downloads carry profile_id on their batch"""
    from core.imports.paths import transfer_root_for_context
    from core.runtime_state import download_batches
    root, _ = share
    k = _profile(db, "k", "KMusic")
    download_batches["b-k"] = {"profile_id": k}
    download_batches["b-admin"] = {"profile_id": 1}
    try:
        assert transfer_root_for_context({"batch_id": "b-k"}) == str(root / "KMusic")
        assert transfer_root_for_context({"batch_id": "b-admin"}) == str(root / "SoulSync" / "organized")
    finally:
        download_batches.pop("b-k", None)
        download_batches.pop("b-admin", None)


def test_final_path_uses_the_same_template_under_the_profile_root(db, share):
    from core.imports.paths import build_final_path_for_track
    root, _ = share
    k = _profile(db, "k", "KMusic")

    def ctx(pid):
        return {"profile_id": pid, "track_info": {"name": "Song", "artists": [{"name": "Art"}]},
                "original_search_result": {"title": "Song", "artists": [{"name": "Art"}]}}

    album = {"is_album": True, "album_name": "Alb", "track_number": 1, "disc_number": 1}
    k_path, _ = build_final_path_for_track(ctx(k), {"name": "Art"}, album, ".flac", create_dirs=False)
    a_path, _ = build_final_path_for_track(ctx(1), {"name": "Art"}, album, ".flac", create_dirs=False)
    assert k_path.startswith(str(root / "KMusic") + "/")
    assert a_path.startswith(str(root / "SoulSync" / "organized") + "/")
    assert k_path[len(str(root / "KMusic")):] == a_path[len(str(root / "SoulSync" / "organized")):]


def test_root_not_derivable_falls_back_to_the_shared_folder(db, tmp_path, monkeypatch):
    from core.imports.paths import transfer_root_for_context
    from core.settings import config_manager
    real_get = config_manager.get
    cfg = {"soulseek.transfer_path": str(tmp_path / "elsewhere" / "organized"), "library.music_paths": []}
    monkeypatch.setattr(config_manager, "get", lambda k, d=None: cfg.get(k, real_get(k, d)))
    k = _profile(db, "k", "KMusic")
    assert transfer_root_for_context({"profile_id": k}) == str(tmp_path / "elsewhere" / "organized")


def test_prefix_cannot_escape_the_share_root(db, share):
    from core.profile_library_folder import folder_root_for_profile
    k = _profile(db, "k", "KMusic")
    with sqlite3.connect(str(db.database_path)) as c:
        c.execute("UPDATE profiles SET library_path_prefix = '../evil' WHERE id = ?", (k,))
    assert folder_root_for_profile(k, db) is None
