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
