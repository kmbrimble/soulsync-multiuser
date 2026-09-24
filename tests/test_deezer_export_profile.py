"""Deezer playlist export must write to the REQUESTING profile's Deezer account,
never silently into the global ARL owner's. Fake ARLs only; no network."""

import flask
import pytest

import api.artist_watchlist as ws
import core.deezer_download_client as ddc
import core.profile_deezer as pd


class _FakeDeezer:
    instances = []

    def __init__(self, arl=None):
        self.arl = arl
        _FakeDeezer.instances.append(self)

    def is_authenticated(self):
        return True


class _ArlDB:
    def __init__(self, arls):
        self.arls = arls

    def get_profile_deezer_arl(self, pid):
        return self.arls.get(pid)


@pytest.fixture
def export(monkeypatch):
    """Runs the deezer export worker for a profile; returns (client_used, job)."""
    _FakeDeezer.instances = []
    monkeypatch.setattr(ddc, 'DeezerDownloadClient', _FakeDeezer)
    monkeypatch.setattr(pd, '_clients', {})
    monkeypatch.setattr(pd, 'get_database', lambda: _ArlDB({2: 'fake-arl-k', 3: None}))
    used = []
    monkeypatch.setattr(ws, 'get_database', lambda: object())
    monkeypatch.setattr(ws, '_run_service_export',
                        lambda job, db, pid, title, mode, client: used.append(client))

    def run(profile_id):
        ws._playlist_export_jobs['j'] = job = {'phase': 'starting', 'error': None}
        ws._run_playlist_export('j', 1, 'PL', 'deezer', profile_id=profile_id)
        return used, job
    return run


def test_profile_with_own_arl_exports_via_its_own_client(export):
    used, job = export(2)
    assert [c.arl for c in used] == ['fake-arl-k']
    assert job['error'] is None


def test_profile_without_arl_is_refused_and_global_never_used(export):
    used, job = export(3)
    assert used == []
    assert job['phase'] == 'error'
    assert 'My Accounts' in job['error']
    assert not [c for c in _FakeDeezer.instances if c.arl is None]   # no global-ARL client built


def test_unreadable_profile_row_is_refused(export, monkeypatch):
    def boom(pid):
        raise RuntimeError('db down')
    monkeypatch.setattr(pd, 'get_database', lambda: type('D', (), {'get_profile_deezer_arl': staticmethod(boom)})())
    used, job = export(2)
    assert used == [] and job['phase'] == 'error'


def test_admin_keeps_upstream_global_client(export):
    used, job = export(1)
    assert len(used) == 1 and used[0].arl is None      # DeezerDownloadClient() as before
    assert job['error'] is None


def test_start_route_passes_captured_profile_to_worker(monkeypatch):
    monkeypatch.setattr(ws, 'get_current_profile_id', lambda: 3)
    monkeypatch.setattr(ws, 'get_database', lambda: object())
    monkeypatch.setattr(ws, '_owned_mirrored_playlist', lambda db, pid: {'name': 'PL'})
    started = {}

    class _T:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            started.update(target=target, args=args, kwargs=kwargs or {})

        def start(self):
            pass
    monkeypatch.setattr(ws.threading, 'Thread', _T)
    with flask.Flask(__name__).test_request_context(json={}):
        ws.start_playlist_export_service('5', 'deezer')
    assert started['target'] is ws._run_playlist_export
    assert started['kwargs'].get('profile_id') == 3 or 3 in started['args'][4:]
