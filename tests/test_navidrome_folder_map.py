"""Navidrome native API -> track_library_folder map (mocked HTTP, temp DB)."""

from __future__ import annotations

import sqlite3

import pytest
import requests

import core.navidrome_folder_map as nfm
from core.profile_context import reset_background_profile, set_background_profile
from database.music_database import MusicDatabase


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


class _FakeHttp:
    def __init__(self, songs, fail_first=0):
        self.songs, self.fail_first, self.calls, self.logins = songs, fail_first, [], []

    def post(self, url, json=None, timeout=None):
        self.logins.append(url)
        return _Resp({'token': 'fake-jwt'})

    def get(self, url, headers=None, params=None, timeout=None):
        assert headers == {'X-ND-Authorization': 'Bearer fake-jwt'}
        if self.fail_first:
            self.fail_first -= 1
            return _Resp([], 503)
        self.calls.append(params['_start'])
        return _Resp(self.songs[params['_start']:params['_end']])


def _songs(n):
    return [{'id': f'id{i}', 'path': ('KMusic' if i % 2 else '/TMusic') + f'/A/B/{i}.mp3'} for i in range(n)]


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'ArtK', 'navidrome')")
    c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (10, 1, 'AK', 'navidrome')")
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) "
              "VALUES ('n1', 10, 1, 't', 'navidrome', 'ArtK/AK/01 - t.mp3')")
    c.commit()
    c.close()
    return d


@pytest.fixture()
def navidrome(monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get_active_media_server', lambda: 'navidrome')
    monkeypatch.setattr(config_manager, 'get_navidrome_config',
                        lambda: {'base_url': 'http://nd', 'username': 'u', 'password': 'fake-pw'})


_n = iter(range(1000))


def _visible_to(db, prefix):
    pid = db.create_profile(name='p_' + prefix + '_' + str(next(_n)))
    db.set_profile_library_prefix(pid, prefix)
    tok = set_background_profile(pid)
    try:
        return [a['name'] for a in db.get_library_artists(page=1, limit=50, profile_id=pid)['artists']]
    finally:
        reset_background_profile(tok)


def test_fetch_paginates_and_maps_ids_to_relative_paths(monkeypatch):
    monkeypatch.setattr(nfm, 'PAGE', 3)
    http = _FakeHttp(_songs(7))
    out = nfm.fetch_song_paths('http://nd:4533/', 'u', 'p', http=http, sleep=lambda s: None)
    assert http.calls == [0, 3, 6] and len(out) == 7
    assert out['id1'] == 'KMusic/A/B/1.mp3' and out['id2'] == 'TMusic/A/B/2.mp3'   # leading '/' stripped
    assert http.logins == ['http://nd:4533/auth/login']


def test_fetch_retries_transient_errors():
    http = _FakeHttp(_songs(2), fail_first=2)
    assert len(nfm.fetch_song_paths('http://nd', 'u', 'p', http=http, sleep=lambda s: None)) == 2


def test_refresh_replaces_map_and_scoping_follows(db, navidrome):
    assert _visible_to(db, 'KMusic') == []                 # nothing mapped yet: hidden, never leaked
    ok = nfm.refresh_track_folders(db, http=_FakeHttp([{'id': 'n1', 'path': 'KMusic/ArtK/AK/01 - t.mp3'}]),
                                   sleep=lambda s: None)
    assert ok == 1
    assert _visible_to(db, 'KMusic') == ['ArtK']
    assert _visible_to(db, 'TMusic') == []


def test_refresh_failure_keeps_old_map_and_never_logs_secrets(db, navidrome, caplog):
    db.replace_track_folders({'n1': 'KMusic/x.mp3'})
    with caplog.at_level('DEBUG'):
        assert nfm.refresh_track_folders(db, http=_FakeHttp(_songs(2), fail_first=99), sleep=lambda s: None) is None
    assert 'fake-pw' not in caplog.text and 'fake-jwt' not in caplog.text
    c = sqlite3.connect(str(db.database_path))
    assert c.execute("SELECT rel_path FROM track_library_folder").fetchall() == [('KMusic/x.mp3',)]


@pytest.mark.parametrize("server, has_prefix, expect_call", [
    ('navidrome', True, True), ('navidrome', False, False), ('plex', True, False),
])
def test_post_sync_hook_refreshes_only_when_needed(monkeypatch, server, has_prefix, expect_call):
    from types import SimpleNamespace

    from core.database_update_worker import DatabaseUpdateWorker
    calls = []
    monkeypatch.setattr(nfm, 'refresh_track_folders', lambda database=None, **k: calls.append(database))
    stub = SimpleNamespace(server_type=server, database=SimpleNamespace(any_profile_library_prefix=lambda: has_prefix))
    DatabaseUpdateWorker._refresh_folder_map(stub)
    assert bool(calls) is expect_call


def test_post_sync_hook_never_raises(monkeypatch):
    from types import SimpleNamespace

    from core.database_update_worker import DatabaseUpdateWorker

    def boom():
        raise RuntimeError("db down")
    DatabaseUpdateWorker._refresh_folder_map(
        SimpleNamespace(server_type='navidrome', database=SimpleNamespace(any_profile_library_prefix=boom)))


def test_refresh_skipped_when_not_navidrome(db, monkeypatch):
    from core.settings import config_manager
    monkeypatch.setattr(config_manager, 'get_active_media_server', lambda: 'plex')
    assert nfm.refresh_track_folders(db, http=_FakeHttp([])) is None
