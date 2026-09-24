"""Per-profile library folder: profile K sees only ``KMusic/`` on the Library
page, T only ``TMusic/``; admin and unset profiles see everything.

Real (temp) DB, no network. Playlist sync / matching must NOT take the prefix.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.profile_context import reset_background_profile, set_background_profile
from database.music_database import MusicDatabase

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    artists = [(1, "ArtK"), (2, "ArtT"), (3, "Both"), (4, "Old"), (5, "Slash"), (6, "Shared"), (7, "Unmapped")]
    c.executemany("INSERT INTO artists (id, name, server_source) VALUES (?, ?, 'plex')", artists)
    # (album id, artist id, title, [paths])
    albums = [
        (10, 1, "AK", ["KMusic/ArtK/AK/1.flac"]),
        (11, 2, "AT", ["TMusic/ArtT/AT/1.flac", "TMusic/ArtT/AT/2.flac"]),
        (12, 3, "B1", ["KMusic/Both/B1/1.flac", "KMusic/Both/B1/2.flac"]),
        (13, 3, "B2", ["TMusic/Both/B2/1.flac"]),
        (14, 4, "OldAlbum", ["KMusicOld/Old/O/1.flac"]),          # boundary: not KMusic
        (15, 5, "SlashAlbum", ["/KMusic/Slash/S/1.flac"]),          # leading-slash storage
        (16, 6, "SoulSyncAlbum", ["SoulSync/organized/Shared/S/1.flac"]),
        (17, 7, "NoMapAlbum", [None]),                              # never mapped yet
    ]
    n = 0
    for alb, art, title, paths in albums:
        c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, 'plex')", (alb, art, title))
        for p in paths:
            n += 1
            # file_path is Navidrome's Subsonic FAKE path (no top-level folder);
            # the real location lives only in track_library_folder
            c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) "
                      "VALUES (?, ?, ?, ?, 'plex', ?)", (str(n), alb, art, f"t{n}", f"Fake Artist/Fake Album/{n:02d} - t{n}.flac"))
            if p:
                c.execute("INSERT INTO track_library_folder (track_id, rel_path) VALUES (?, ?)", (str(n), p))
    c.commit()
    c.close()
    return d


def _profile(db, name, prefix=None):
    pid = db.create_profile(name=name)
    if prefix is not None:
        assert db.set_profile_library_prefix(pid, prefix)
    return pid


class _As:
    def __init__(self, pid):
        self.pid = pid

    def __enter__(self):
        self.tok = set_background_profile(self.pid)

    def __exit__(self, *a):
        reset_background_profile(self.tok)


def _page(db, pid):
    with _As(pid):
        return db.get_library_artists(page=1, limit=50, profile_id=pid)


def _names(r):
    return sorted(a["name"] for a in r["artists"])


# ── artists list ─────────────────────────────────────────────────────────────

def test_admin_sees_everything(db):
    r = _page(db, 1)
    assert _names(r) == ["ArtK", "ArtT", "Both", "Old", "Shared", "Slash", "Unmapped"]
    assert r["pagination"]["total_count"] == 7     # unmapped tracks show for admin as normal


def test_unset_prefix_is_unchanged(db):
    pid = _profile(db, "plain")
    assert _page(db, pid) == _page(db, 1)


def test_k_sees_only_kmusic_with_scoped_counts(db):
    k = _profile(db, "k", "KMusic")
    r = _page(db, k)
    assert _names(r) == ["ArtK", "Both", "Slash"]           # not KMusicOld, not T/SoulSync
    assert r["pagination"]["total_count"] == 3
    both = next(a for a in r["artists"] if a["name"] == "Both")
    assert (both["album_count"], both["track_count"]) == (1, 2)


def test_t_sees_only_tmusic(db):
    t = _profile(db, "t", "TMusic")
    r = _page(db, t)
    assert _names(r) == ["ArtT", "Both"]
    both = next(a for a in r["artists"] if a["name"] == "Both")
    assert (both["album_count"], both["track_count"]) == (1, 1)


def test_prefix_is_case_as_stored_and_trailing_slash_tolerant(db):
    assert _names(_page(db, _profile(db, "lower", "kmusic"))) == []
    assert _names(_page(db, _profile(db, "slashy", "KMusic/"))) == ["ArtK", "Both", "Slash"]


def test_prefix_can_be_cleared(db):
    k = _profile(db, "k", "KMusic")
    assert db.set_profile_library_prefix(k, "")
    assert db.get_profile_library_prefix(k) == ""
    assert len(_page(db, k)["artists"]) == 7


def test_admin_profile_can_never_be_scoped(db):
    assert db.set_profile_library_prefix(1, "KMusic") is False
    assert db.get_profile_library_prefix(1) == ""
    assert len(_page(db, 1)["artists"]) == 7


def test_unmapped_tracks_are_hidden_from_scoped_profiles(db):
    for prefix in ("KMusic", "TMusic"):
        assert "Unmapped" not in _names(_page(db, _profile(db, "p" + prefix, prefix)))
    with _As(_profile(db, "kk", "KMusic")):
        assert db.get_artist_discography(7)["success"] is False


def test_file_path_is_never_touched(db):
    _page(db, _profile(db, "k", "KMusic"))
    c = sqlite3.connect(str(db.database_path))
    assert all(p.startswith("Fake Artist/Fake Album/") for (p,) in c.execute("SELECT file_path FROM tracks"))


# ── artist detail (albums / tracks) ──────────────────────────────────────────

def test_discography_is_scoped(db):
    k = _profile(db, "k", "KMusic")
    with _As(k):
        r = db.get_artist_discography(3)
        titles = [a["title"] for grp in r["owned_releases"].values() for a in grp]
        assert titles == ["B1"]
        assert r["artist"]["album_count"] == 1 and r["artist"]["track_count"] == 2
        # an artist with nothing under KMusic is not reachable by id
        assert db.get_artist_discography(2)["success"] is False
    with _As(1):
        assert len([a for g in db.get_artist_discography(3)["owned_releases"].values() for a in g]) == 2


def test_full_detail_is_scoped(db):
    t = _profile(db, "t", "TMusic")
    with _As(t):
        r = db.get_artist_full_detail(3)
        assert [a["title"] for a in r["albums"]] == ["B2"]
        assert [tr["id"] for tr in r["albums"][0]["tracks"]] == ["6"]      # the one TMusic track
        assert db.get_artist_full_detail(1)["success"] is False
    with _As(1):
        assert len(db.get_artist_full_detail(3)["albums"]) == 2


# ── matching / sync must not take the prefix ─────────────────────────────────

def test_track_search_used_by_matching_ignores_prefix(db):
    k = _profile(db, "k", "KMusic")
    with _As(k):
        hits = db.search_tracks(title="t3", artist="")
    assert [h.id for h in hits] == ["3"], "K's playlist match must still see TMusic (AT track 2)"


@pytest.mark.parametrize("rel", [
    "services/sync_service.py", "core/wishlist/processing.py", "core/library_scope.py",
    "core/matching_engine.py", "core/navidrome_client.py",
])
def test_sync_and_matching_code_never_reference_the_prefix(rel):
    src = (ROOT / rel).read_text()
    for banned in ("library_path_prefix", "profile_library_folder", "track_library_folder", "navidrome_folder_map"):
        assert banned not in src, banned


def test_shared_scope_sql_does_not_take_prefix(db):
    k = _profile(db, "k", "KMusic")
    with _As(k):
        sql, params = db._current_scope_sql('a.owner_profile_id')
    assert "file_path" not in sql and "track_library_folder" not in sql and params == []


def test_unmatched_banner_is_scoped(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO artists (id, name, server_source) VALUES (8, 'Unknown Artist', 'plex')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (18, 8, 'U', 'plex')")
    for tid, path in (("u1", "KMusic/u/1.mp3"), ("u2", "TMusic/u/2.mp3"), ("u3", "TMusic/u/3.mp3")):
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source) VALUES (?, 18, 8, 'x', 'plex')", (tid,))
        c.execute("INSERT INTO track_library_folder (track_id, rel_path) VALUES (?, ?)", (tid, path))
    c.commit()
    c.close()
    with _As(1):
        assert db.get_unmatched_import_summary()["count"] == 3
    with _As(_profile(db, "k", "KMusic")):
        assert db.get_unmatched_import_summary()["count"] == 1
    with _As(_profile(db, "t", "TMusic")):
        assert db.get_unmatched_import_summary()["count"] == 2


# ── admin API ────────────────────────────────────────────────────────────────

def test_profiles_api_sets_and_reports_prefix(tmp_path, monkeypatch):
    import os
    import tempfile
    os.environ.setdefault('DATABASE_PATH', os.path.join(tempfile.mkdtemp(prefix='soulsync-testdb-libfolder-'), 'x.db'))
    os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')
    web_server = pytest.importorskip('web_server')
    import core.navidrome_folder_map as nfm
    kicked = []
    monkeypatch.setattr(nfm, 'refresh_in_background', lambda database=None: kicked.append(1))
    wdb = web_server.get_database()
    pid = wdb.create_profile(name='k_' + os.urandom(3).hex())
    other = wdb.create_profile(name='o_' + os.urandom(3).hex())
    client = web_server.app.test_client()

    def as_(p):
        with client.session_transaction() as s:
            s['profile_id'] = p

    as_(1)
    r = client.put(f'/api/profiles/{pid}', json={'library_path_prefix': ' KMusic/ '})
    assert r.get_json()['success'] is True
    assert wdb.get_profile_library_prefix(pid) == 'KMusic'
    assert kicked == [1]          # setting a folder refreshes the map
    listed = {p['id']: p for p in client.get('/api/profiles').get_json()['profiles']}
    assert listed[pid]['library_path_prefix'] == 'KMusic'
    assert client.put('/api/profiles/1', json={'library_path_prefix': 'KMusic'}).status_code == 400

    as_(pid)   # a non-admin cannot scope themselves (or anyone) differently
    client.put(f'/api/profiles/{pid}', json={'library_path_prefix': ''})
    assert wdb.get_profile_library_prefix(pid) == 'KMusic'
    client.put(f'/api/profiles/{other}', json={'library_path_prefix': 'X'})
    assert wdb.get_profile_library_prefix(other) == ''
