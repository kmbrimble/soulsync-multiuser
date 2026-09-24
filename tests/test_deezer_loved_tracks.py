"""Deezer Loved tracks: carried through and labelled/first in the ARL list.

No network: the download client's HTTP layer and the per-profile client are faked.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-deezerloved-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'deezerloved.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')

from core.deezer_download_client import DeezerDownloadClient  # noqa: E402


class _Resp:
    ok = True

    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


PLAYLISTS = {'data': [
    {'id': 11, 'title': 'Road trip', 'nb_tracks': 3, 'is_loved_track': False},
    {'id': 22, 'title': 'Favourite tracks', 'nb_tracks': 9, 'is_loved_track': True},
]}


def test_download_client_keeps_is_loved_track():
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._authenticated, c._user_data = True, {'USER_ID': 5}
    c._api_get = lambda *a, **k: _Resp(PLAYLISTS)
    got = c.get_user_playlists()
    assert [(p['id'], p['is_loved_track']) for p in got] == [('11', False), ('22', True)]


def test_arl_playlists_label_and_list_loved_first(monkeypatch):
    class Fake:
        def is_authenticated(self):
            return True

        def get_user_playlists(self):
            return [{'id': '11', 'name': 'Road trip', 'track_count': 3},
                    {'id': '22', 'name': 'Favourite tracks', 'track_count': 9,
                     'is_loved_track': True}]

    import api.source_playlists as sp
    monkeypatch.setattr(sp, '_deezer_dl_for_profile', lambda: Fake())
    body = web_server.app.test_client().get('/api/deezer/arl-playlists').get_json()
    assert [(p['id'], p['name']) for p in body] == [('22', 'Loved tracks'), ('11', 'Road trip')]
    assert [p['is_loved_track'] for p in body] == [True, False]


# ── private profiles: the public API refuses the ARL, the gateway does not ────

PRIVATE = {'error': {'type': 'OAuthException', 'code': 200,
                     'message': "This user's profile is private"}}


def _client(gw, api=None):
    c = DeezerDownloadClient.__new__(DeezerDownloadClient)
    c._authenticated = True
    c._user_data = {'USER_ID': 5, 'BLOG_NAME': 'k', 'LOVEDTRACKS_ID': 999}
    c._session = object()
    calls = []

    def gw_call(method, params=None):
        calls.append((method, params))
        return gw(method, params or {})
    c._gw_call = gw_call
    c._api_get = api or (lambda *a, **k: _Resp(PRIVATE))
    c.calls = calls
    return c


SONG = {'SNG_ID': '77', 'SNG_TITLE': 'Song', 'ART_NAME': 'Art', 'ALB_ID': '8',
        'ALB_TITLE': 'Alb', 'ALB_PICTURE': 'abc', 'DURATION': '200', 'TRACK_NUMBER': '4'}


def _gw(method, params):
    if method == 'deezer.pageProfile':
        return {'TAB': {'playlists': {'total': 1, 'data': [
            {'PLAYLIST_ID': '31', 'TITLE': 'Private mix', 'NB_SONG': 2,
             'PLAYLIST_PICTURE': 'pic', 'PICTURE_TYPE': 'playlist'}]}}}
    if method == 'playlist.getSongs':
        return {'total': 99 if params.get('nb') == 1 else 1, 'data': [SONG]}
    if method == 'playlist.getData':
        return {'DATA': {'TITLE': 'Private mix'}}


def test_private_profile_lists_gateway_playlists_with_loved_first():
    c = _client(_gw, api=lambda *a, **k: pytest.fail('gateway should be enough'))
    got = c.get_user_playlists()
    assert [(p['id'], p['name'], p['track_count'], p['is_loved_track']) for p in got] == [
        ('999', 'Loved tracks', 99, True), ('31', 'Private mix', 2, False)]
    assert got[1]['image_url'].endswith('/playlist/pic/250x250-000000-80-0-0.jpg')


def test_playlists_fall_back_to_public_api_when_gateway_is_empty():
    c = _client(lambda m, p: None, api=lambda *a, **k: _Resp(PLAYLISTS))
    assert [p['id'] for p in c.get_user_playlists()] == ['11', '22']


def test_unauthenticated_client_lists_nothing():
    c = _client(_gw)
    c._authenticated = False
    assert c.get_user_playlists() == [] and c.calls == []


def test_loved_tracks_fall_back_to_gateway_in_public_shape(monkeypatch):
    import core.deezer_client as dc
    monkeypatch.setattr(dc, 'resolve_album_track_positions', lambda *a, **k: {})
    c = _client(_gw)
    c._api_get = lambda url, **k: _Resp(PRIVATE) if '/playlist/' in url else None
    out = c.get_playlist_tracks('999')
    assert (out['name'], out['track_count']) == ('Loved tracks', 1)
    t = out['tracks'][0]
    assert (t['id'], t['name'], t['track_number'], t['duration_ms']) == ('77', 'Song', 4, 200000)
    assert t['artists'] == [{'name': 'Art'}]
    assert t['album']['id'] == '8'
    assert t['album']['images'][0]['url'].endswith('abc/250x250-000000-80-0-0.jpg')


def test_public_playlist_does_not_touch_the_gateway(monkeypatch):
    import core.deezer_client as dc
    monkeypatch.setattr(dc, 'resolve_album_track_positions', lambda *a, **k: {})
    public = {'id': 11, 'title': 'Road trip', 'nb_tracks': 1, 'creator': {'name': 'x'},
              'tracks': {'data': [{'id': 1, 'title': 'T', 'duration': 3,
                                   'artist': {'name': 'A'}, 'album': {'id': 2, 'title': 'B'}}]}}
    c = _client(lambda *a: pytest.fail('gateway used'),
                api=lambda url, **k: _Resp(public) if url.endswith('/playlist/11') else None)
    out = c.get_playlist_tracks('11')
    assert out['name'] == 'Road trip' and out['tracks'][0]['name'] == 'T'
