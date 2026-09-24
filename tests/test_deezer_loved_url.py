"""Pasting a Deezer Loved-tracks link: URL parsing + /api/deezer/resolve-loved.

Own account -> the profile's ARL client and its LOVEDTRACKS_ID; someone else's
profile -> the public API (which only serves it if their profile is public).
Real app + real HTTP + real (temp) DB; the Deezer clients are fakes, so nothing
touches the network. ARLs here are obvious placeholders.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-deezerloved-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'deezerloved.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.deezer_client import DeezerClient  # noqa: E402

ARL_K = 'FAKE-ARL-FOR-K-0000'
ARL_T = 'FAKE-ARL-FOR-T-1111'
USERS = {ARL_K: (111, 'K-LOVED'), ARL_T: (222, 'T-LOVED'), 'GLOBAL': (999, 'G-LOVED')}


class FakeDeezer:
    def __init__(self, download_path=None, arl=None):
        self.arl = arl

    def is_authenticated(self):
        return True

    @property
    def _user_data(self):
        uid, loved = USERS[self.arl]
        return {'USER_ID': uid, 'LOVEDTRACKS_ID': loved, 'BLOG_NAME': 'x'}


class GlobalDeezer(FakeDeezer):
    def __init__(self):
        super().__init__(arl='GLOBAL')


class FakeOrchestrator:
    def __init__(self, g):
        self.g = g

    def client(self, name):
        return self.g if name == 'deezer_dl' else None


class FakePublic:
    """Stands in for the public DeezerClient."""
    def __init__(self, loved=None):
        self.loved = loved or {}
        self.asked = []

    def get_public_loved_playlist_id(self, user_id):
        self.asked.append(user_id)
        return self.loved.get(str(user_id))


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    import core.deezer_download_client as ddc
    from core import profile_deezer
    import api.source_playlists as sp
    monkeypatch.setattr(ddc, 'DeezerDownloadClient', FakeDeezer)
    profile_deezer._clients.clear()
    monkeypatch.setattr(sp, 'download_orchestrator', FakeOrchestrator(GlobalDeezer()))
    public = FakePublic({'555': '5550001'})
    monkeypatch.setattr(sp, '_get_deezer_client', lambda: public)
    yield public
    profile_deezer._clients.clear()


@pytest.fixture
def client():
    return web_server.app.test_client()


@pytest.fixture
def db():
    return web_server.get_database()


def _profile(db, label, arl=None):
    pid = db.create_profile(name=f'{label}_{os.urandom(3).hex()}')
    if arl:
        db.set_profile_deezer_arl(pid, arl)
    return pid


def _resolve(client, pid, url):
    with client.session_transaction() as sess:
        sess['profile_id'] = pid
    return client.get('/api/deezer/resolve-loved', query_string={'url': url})


# ── parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize('url,expected', [
    ('https://www.deezer.com/en/profile/12345/loved', '12345'),
    ('https://www.deezer.com/profile/12345/loved', '12345'),
    ('https://www.deezer.com/en-us/profile/12345/loved', '12345'),
    ('www.deezer.com/fr/profile/12345/loved', '12345'),
    ('deezer.com/profile/12345/loved?utm_source=x', '12345'),
    ('https://www.deezer.com/en/library/loved', 'me'),
    ('https://www.deezer.com/en/playlist/12345', None),
    ('https://www.deezer.com/en/profile/12345', None),
    ('https://www.deezer.com/en/profile/12345/lovedish', None),
    ('https://example.com/en/profile/12345/loved', None),
    ('12345', None),
    ('', None),
    (None, None),
])
def test_parse_loved_url(url, expected):
    assert DeezerClient.parse_loved_url(url) == expected


def test_loved_url_is_not_a_playlist_url():
    assert DeezerClient.parse_playlist_url('https://www.deezer.com/en/profile/1/loved') is None


def test_public_loved_lookup_pages_and_picks_the_loved_entry():
    calls = []

    class C(DeezerClient):
        def __init__(self):
            pass

        def _api_get(self, endpoint, params=None, **kw):
            calls.append((endpoint, dict(params or {}), kw))
            if params['index'] == 0:
                return {'data': [{'id': i, 'is_loved_track': False} for i in range(100)], 'next': 'x'}
            return {'data': [{'id': 777, 'is_loved_track': True}]}

    assert C().get_public_loved_playlist_id('42') == '777'
    assert [c[1]['index'] for c in calls] == [0, 100]
    assert calls[0][0] == 'user/42/playlists'
    assert calls[0][2].get('use_token') is False   # someone else's profile: no stale OAuth token


def test_public_loved_lookup_none_for_private_or_missing():
    class C(DeezerClient):
        def __init__(self):
            pass

        def _api_get(self, endpoint, params=None, **kw):
            return None

    assert C().get_public_loved_playlist_id('42') is None


# ── resolution ───────────────────────────────────────────────────────────────

def test_own_profile_url_resolves_to_that_profiles_loved_id(client, db, fakes):
    k = _profile(db, 'k', ARL_K)
    r = _resolve(client, k, 'https://www.deezer.com/en/profile/111/loved')
    assert r.status_code == 200
    assert r.get_json() == {'kind': 'own', 'playlist_id': 'K-LOVED'}
    assert fakes.asked == []   # own account never goes to the public API


def test_library_loved_resolves_to_own(client, db):
    k = _profile(db, 'k', ARL_K)
    r = _resolve(client, k, 'https://www.deezer.com/en/library/loved')
    assert r.get_json() == {'kind': 'own', 'playlist_id': 'K-LOVED'}


def test_profile_isolation_each_profile_gets_its_own_loved(client, db):
    k = _profile(db, 'k', ARL_K)
    t = _profile(db, 't', ARL_T)
    assert _resolve(client, k, 'https://www.deezer.com/profile/111/loved').get_json()['playlist_id'] == 'K-LOVED'
    assert _resolve(client, t, 'https://www.deezer.com/en/library/loved').get_json()['playlist_id'] == 'T-LOVED'
    # T pasting K's own-account URL: not T's account, so it is a public lookup, never K's private list
    r = _resolve(client, t, 'https://www.deezer.com/profile/111/loved')
    assert r.status_code == 404


def test_admin_uses_global_client(client):
    r = _resolve(client, 1, 'https://www.deezer.com/en/library/loved')
    assert r.get_json() == {'kind': 'own', 'playlist_id': 'G-LOVED'}
    r = _resolve(client, 1, 'https://www.deezer.com/en/profile/999/loved')
    assert r.get_json() == {'kind': 'own', 'playlist_id': 'G-LOVED'}


def test_other_users_public_profile_resolves_via_public_api(client, db, fakes):
    k = _profile(db, 'k', ARL_K)
    r = _resolve(client, k, 'https://www.deezer.com/en/profile/555/loved')
    assert r.status_code == 200
    assert r.get_json() == {'kind': 'public', 'playlist_id': '5550001'}
    assert fakes.asked == ['555']


def test_other_users_public_profile_needs_no_arl(client, db):
    u = _profile(db, 'noarl')   # unconfigured non-admin -> global client, not their own account
    r = _resolve(client, u, 'https://www.deezer.com/en/profile/555/loved')
    assert r.get_json() == {'kind': 'public', 'playlist_id': '5550001'}


def test_other_users_private_profile_is_a_clear_error(client, db):
    k = _profile(db, 'k', ARL_K)
    r = _resolve(client, k, 'https://www.deezer.com/en/profile/424242/loved')
    assert r.status_code == 404
    assert 'private' in r.get_json()['error'].lower()


def test_non_loved_url_is_rejected(client, db):
    k = _profile(db, 'k', ARL_K)
    r = _resolve(client, k, 'https://www.deezer.com/en/playlist/1')
    assert r.status_code == 400


def test_non_admin_without_own_arl_is_told_to_connect_never_given_the_global_owners_list(client, db):
    u = _profile(db, 'noarl')
    for url in ('https://www.deezer.com/en/library/loved', 'https://www.deezer.com/en/profile/999/loved'):
        r = _resolve(client, u, url)
        assert 'G-LOVED' not in r.get_data(as_text=True)
    r = _resolve(client, u, 'https://www.deezer.com/en/library/loved')
    assert r.status_code == 401
    assert 'My Accounts' in r.get_json()['error']


def test_unreadable_arl_row_fails_closed(client, db, monkeypatch):
    k = _profile(db, 'k', ARL_K)
    monkeypatch.setattr(type(db), 'get_profile_deezer_arl', lambda self, pid: (_ for _ in ()).throw(RuntimeError('boom')))
    r = _resolve(client, k, 'https://www.deezer.com/en/library/loved')
    assert r.status_code == 401
    assert 'K-LOVED' not in r.get_data(as_text=True)
