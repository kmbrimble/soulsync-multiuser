"""Per-profile Deezer ARL: each profile reads ITS OWN Deezer account.

Real app + real HTTP + real (temp) DB; the Deezer clients are fakes, so nothing
touches the network. ARLs here are obvious placeholders.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-deezerarl-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'deezerarl.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

ARL_K = 'FAKE-ARL-FOR-K-0000'
ARL_T = 'FAKE-ARL-FOR-T-1111'


class FakeDeezer:
    """Stands in for DeezerDownloadClient; remembers which ARL it was built with."""
    built = []

    def __init__(self, download_path=None, arl=None):
        self.arl = arl
        self.name = 'user-' + str(arl).split('FOR-')[-1]   # must not embed the ARL itself
        FakeDeezer.built.append(arl)

    def is_authenticated(self):
        return True

    @property
    def _user_data(self):
        return {'BLOG_NAME': self.name, 'USER_ID': 7}

    def get_user_playlists(self):
        return [{'id': f'pl-{self.arl}', 'name': f'{self.name} mix', 'owner': self.name,
                 'track_count': 1, 'image_url': ''}]

    def get_playlist_tracks(self, playlist_id, progress_cb=None):
        return {'id': playlist_id, 'name': f'{self.name} tracks', 'tracks': [{'name': 't'}]}

    def get_user_favorite_artists(self, limit=200):
        return [{'name': f'artist-of-{self.arl}', 'deezer_id': '1'}]

    def get_user_favorite_albums(self, limit=200):
        return [{'album_name': f'album-of-{self.arl}', 'artist_name': 'x', 'deezer_id': '1'}]


class GlobalDeezer(FakeDeezer):
    def __init__(self):
        super().__init__(arl='GLOBAL')


class FakeOrchestrator:
    def __init__(self, g):
        self.g = g

    def client(self, name):
        return self.g if name == 'deezer_dl' else None


@pytest.fixture(autouse=True)
def fake_deezer(monkeypatch):
    import core.deezer_download_client as ddc
    from core import profile_deezer
    FakeDeezer.built = []
    monkeypatch.setattr(ddc, 'DeezerDownloadClient', FakeDeezer)
    profile_deezer._clients.clear()
    yield
    profile_deezer._clients.clear()


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def db():
    return web_server.get_database()


def _profile(db, label):
    return db.create_profile(name=f'{label}_{os.urandom(3).hex()}')


@pytest.fixture
def orchestrator(monkeypatch):
    g = GlobalDeezer()
    orch = FakeOrchestrator(g)
    import api.discover_routes as disc
    import api.source_playlists as sp
    monkeypatch.setattr(sp, 'download_orchestrator', orch)
    monkeypatch.setattr(disc, 'download_orchestrator', orch)
    return g


def _as(client, pid):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid


# ── DB ───────────────────────────────────────────────────────────────────────

def test_arl_round_trips_encrypted(db):
    pid = _profile(db, 'k')
    assert db.get_profile_deezer_arl(pid) is None
    assert db.set_profile_deezer_arl(pid, ARL_K)
    assert db.get_profile_deezer_arl(pid) == ARL_K
    with db._get_connection() as conn:
        raw = conn.execute("SELECT deezer_arl FROM profiles WHERE id = ?", (pid,)).fetchone()[0]
    assert raw and ARL_K not in raw
    assert db.set_profile_deezer_arl(pid, None)
    assert db.get_profile_deezer_arl(pid) is None


# ── resolver ─────────────────────────────────────────────────────────────────

def test_admin_and_unconfigured_use_global(db):
    from core.profile_deezer import resolve_deezer_dl_client
    g = object()
    other = _profile(db, 'unconfigured')
    assert resolve_deezer_dl_client(g, 1) is g
    assert resolve_deezer_dl_client(g, other) is g
    assert FakeDeezer.built == []


def test_configured_profile_gets_dedicated_cached_client(db):
    from core.profile_deezer import resolve_deezer_dl_client
    g = object()
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    c = resolve_deezer_dl_client(g, k)
    assert c is not g and c.arl == ARL_K
    assert resolve_deezer_dl_client(g, k) is c
    assert FakeDeezer.built == [ARL_K]


def test_changing_or_removing_the_arl_drops_the_cached_client(db):
    from core.profile_deezer import resolve_deezer_dl_client
    g = object()
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    first = resolve_deezer_dl_client(g, k)
    db.set_profile_deezer_arl(k, ARL_T)
    second = resolve_deezer_dl_client(g, k)
    assert second is not first and second.arl == ARL_T
    db.set_profile_deezer_arl(k, None)
    assert resolve_deezer_dl_client(g, k) is g


def test_a_failed_login_is_not_cached_so_it_is_retried(db, monkeypatch):
    from core.profile_deezer import resolve_deezer_dl_client
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    monkeypatch.setattr(FakeDeezer, 'is_authenticated', lambda self: False)
    first = resolve_deezer_dl_client(object(), k)
    assert resolve_deezer_dl_client(object(), k) is not first   # rebuilt, not stuck
    assert FakeDeezer.built == [ARL_K, ARL_K]


def test_unreadable_profile_row_never_falls_back_to_global(db, monkeypatch):
    from core import profile_deezer
    k = _profile(db, 'k')
    monkeypatch.setattr(profile_deezer, 'get_database',
                        lambda: (_ for _ in ()).throw(RuntimeError('db down')))
    assert profile_deezer.resolve_deezer_dl_client(object(), k) is None


# ── sync page endpoints ──────────────────────────────────────────────────────

def test_arl_playlists_are_per_profile(client, db, orchestrator):
    k, t, plain = _profile(db, 'k'), _profile(db, 't'), _profile(db, 'plain')
    db.set_profile_deezer_arl(k, ARL_K)
    db.set_profile_deezer_arl(t, ARL_T)
    names = {}
    for label, pid in (('k', k), ('t', t), ('plain', plain), ('admin', 1)):
        _as(client, pid)
        names[label] = [p['name'] for p in client.get('/api/deezer/arl-playlists').get_json()]
    assert names['k'] == [f'user-K-0000 mix']
    assert names['t'] == [f'user-T-1111 mix']
    assert names['plain'] == names['admin'] == ['user-GLOBAL mix']


def test_arl_status_reports_the_profiles_own_account(client, db, orchestrator):
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    _as(client, k)
    assert client.get('/api/deezer/arl-status').get_json()['user_name'] == f'user-K-0000'
    _as(client, 1)
    assert client.get('/api/deezer/arl-status').get_json()['user_name'] == 'user-GLOBAL'


def test_sync_playlist_tracks_use_the_profiles_client(client, db, orchestrator):
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    _as(client, k)
    body = client.get('/api/deezer/arl-playlist/123').get_json()
    assert body['name'] == f'user-K-0000 tracks'


def test_async_job_runs_as_the_requesting_profile(client, db, orchestrator, monkeypatch):
    import api.source_playlists as sp
    k, t = _profile(db, 'k'), _profile(db, 't')
    db.set_profile_deezer_arl(k, ARL_K)
    db.set_profile_deezer_arl(t, ARL_T)
    submitted = []
    monkeypatch.setattr(sp.deezer_discovery_executor, 'submit',
                        lambda fn, *a, **kw: submitted.append((fn, a, kw)))
    sp.deezer_playlist_load_jobs.clear()

    _as(client, k)
    jk = client.get('/api/deezer/arl-playlist/same?async=1').get_json()['job_id']
    _as(client, t)
    jt = client.get('/api/deezer/arl-playlist/same?async=1').get_json()['job_id']
    assert jk != jt, "K and T must not share an in-flight job for the same playlist id"

    # run the workers exactly as the executor would: on a bare thread, no request context
    import threading
    for fn, a, kw in submitted:
        th = threading.Thread(target=fn, args=a, kwargs=kw)
        th.start()
        th.join()
    assert sp.deezer_playlist_load_jobs[jk]['playlist']['name'] == f'user-K-0000 tracks'
    assert sp.deezer_playlist_load_jobs[jt]['playlist']['name'] == f'user-T-1111 tracks'

    # and one profile cannot read the other's finished job
    _as(client, t)
    assert client.get(f'/api/deezer/playlist-load/{jk}').status_code == 404
    assert client.get(f'/api/deezer/playlist-load/{jt}').status_code == 200


# ── discover fallback ────────────────────────────────────────────────────────

def test_discover_fallback_uses_the_profiles_client(db, orchestrator, monkeypatch):
    import api.discover_routes as disc
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)

    class Rec:
        artists, albums = [], []

        def upsert_liked_artist(self, **kw):
            self.artists.append((kw['artist_name'], kw['profile_id']))

        def upsert_liked_album(self, **kw):
            self.albums.append((kw['album_name'], kw['profile_id']))

    rec = Rec()

    class Cfg:
        def get(self, key, default=None):
            return 'deezer' if key.startswith('discover.') else ''

    monkeypatch.setattr(disc, 'get_database', lambda: rec)
    monkeypatch.setattr(disc, 'config_manager', Cfg())
    monkeypatch.setattr(disc, '_spotify_client', lambda: None)
    monkeypatch.setattr(disc, '_tidal_client', lambda: None)
    monkeypatch.setattr(disc, '_get_deezer_client', lambda: None)
    monkeypatch.setattr(disc, '_match_liked_artists_to_all_sources', lambda *a, **kw: None)

    disc._fetch_and_match_liked_artists(k)
    disc._fetch_liked_albums(k)
    assert rec.artists == [(f'artist-of-{ARL_K}', k)]
    assert rec.albums == [(f'album-of-{ARL_K}', k)]


# ── /api/profiles/me/deezer-arl ──────────────────────────────────────────────

@pytest.fixture
def verify(monkeypatch):
    from core import profile_deezer
    calls = []

    def fake_verify(arl):
        calls.append(arl)
        return (True, 'K Deezer') if arl == ARL_K else (False, None)
    monkeypatch.setattr(profile_deezer, 'verify_arl', fake_verify)
    return calls


def test_post_verifies_before_saving_and_returns_display_name(client, db, verify):
    k = _profile(db, 'k')
    _as(client, k)
    r = client.post('/api/profiles/me/deezer-arl', json={'arl': ARL_K})
    assert r.status_code == 200 and r.get_json() == {'success': True, 'user_name': 'K Deezer'}
    assert verify == [ARL_K]
    assert db.get_profile_deezer_arl(k) == ARL_K


def test_post_bad_arl_is_refused_and_not_saved(client, db, verify):
    k = _profile(db, 'k')
    db.set_profile_deezer_arl(k, ARL_K)
    _as(client, k)
    r = client.post('/api/profiles/me/deezer-arl', json={'arl': 'FAKE-BAD-ARL'})
    assert r.status_code == 400 and not r.get_json()['success']
    assert 'FAKE-BAD-ARL' not in r.get_data(as_text=True)
    assert db.get_profile_deezer_arl(k) == ARL_K   # previous one untouched


def test_post_blank_arl_is_400_without_a_login_attempt(client, db, verify):
    _as(client, _profile(db, 'k'))
    assert client.post('/api/profiles/me/deezer-arl', json={'arl': '  '}).status_code == 400
    assert verify == []


def test_get_never_leaks_the_arl(client, db, verify):
    k = _profile(db, 'k')
    _as(client, k)
    assert client.get('/api/profiles/me/deezer-arl').get_json()['configured'] is False
    client.post('/api/profiles/me/deezer-arl', json={'arl': ARL_K})
    r = client.get('/api/profiles/me/deezer-arl')
    assert r.get_json()['configured'] is True
    assert ARL_K not in r.get_data(as_text=True)
    conn = client.get('/api/profiles/me/connections')
    assert ARL_K not in conn.get_data(as_text=True)
    assert conn.get_json()['connections']['deezer']['connected'] is True


def test_delete_clears_and_evicts_the_cached_client(client, db, verify, orchestrator):
    k = _profile(db, 'k')
    _as(client, k)
    client.post('/api/profiles/me/deezer-arl', json={'arl': ARL_K})
    assert client.get('/api/deezer/arl-status').get_json()['user_name'] == f'user-K-0000'
    assert client.delete('/api/profiles/me/deezer-arl').get_json()['success']
    assert db.get_profile_deezer_arl(k) is None
    assert client.get('/api/deezer/arl-status').get_json()['user_name'] == 'user-GLOBAL'


def test_admin_cannot_save_a_profile_arl(client, verify):
    _as(client, 1)
    assert client.post('/api/profiles/me/deezer-arl', json={'arl': ARL_K}).status_code == 400
    assert verify == []


def test_arl_is_per_session_profile_and_ignores_body_profile_id(client, db, verify):
    k, t = _profile(db, 'k'), _profile(db, 't')
    _as(client, k)
    client.post('/api/profiles/me/deezer-arl', json={'arl': ARL_K, 'profile_id': t})
    assert db.get_profile_deezer_arl(k) == ARL_K
    assert db.get_profile_deezer_arl(t) is None
    _as(client, t)
    assert client.get('/api/profiles/me/deezer-arl').get_json()['configured'] is False
