"""scripts/copy_profile_playlist_tracks.py: temp share tree + mocked Navidrome HTTP."""

from __future__ import annotations

import csv
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("copy_profile_playlist_tracks",
                                               ROOT / "scripts" / "copy_profile_playlist_tracks.py")
cpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cpt)


class _Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Http:
    """Navidrome native API: login, /api/playlist, /api/playlist/<id>/tracks."""

    def __init__(self, playlists, tracks, login=None):
        self.playlists, self.tracks = playlists, tracks
        self.login = login or {"token": "tok", "id": "u-k", "username": "kim"}
        self.calls = []

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url, json))
        return _Resp(self.login)

    def get(self, url, params=None, headers=None, **kw):
        self.calls.append(("GET", url, params))
        assert headers == {"X-ND-Authorization": "Bearer tok"}
        start, end = params["_start"], params["_end"]
        if url.endswith("/api/playlist"):
            return _Resp(self.playlists[start:end])
        pid = url.split("/api/playlist/")[1].split("/")[0]
        return _Resp(self.tracks[pid][start:end])


@pytest.fixture()
def share(tmp_path):
    root = tmp_path / "music"
    files = {
        "KMusic/Own/Alb/1.flac": b"own",
        "TMusic/ArtT/AT/2.flac": b"tt" * 5,
        "SoulSync/organized/ArtS/AS/3.flac": b"sss",
        "Lidarr/ArtL/AL/4.flac": b"llll",
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


PLAYLISTS = [
    {"id": "p1", "name": "mine", "ownerId": "u-k", "ownerName": "kim"},
    {"id": "p2", "name": "someone else's public one", "ownerId": "u-t", "ownerName": "tee"},
]
TRACKS = {
    "p1": [{"path": "KMusic/Own/Alb/1.flac"}, {"path": "TMusic/ArtT/AT/2.flac"},
           {"path": "SoulSync/organized/ArtS/AS/3.flac"}, {"path": "Lidarr/ArtL/AL/4.flac"},
           {"path": "TMusic/ArtT/AT/2.flac"}],                 # duplicate across playlists/rows
    "p2": [{"path": "TMusic/NotMine/x.flac"}],
}


def _paths(http=None):
    return cpt.playlist_track_paths("http://nd", "kim", "pw", http=http or _Http(PLAYLISTS, TRACKS),
                                    sleep=lambda s: None)


# ── collecting ───────────────────────────────────────────────────────────────

def test_only_the_owners_playlists_are_read_and_paths_deduplicated():
    assert _paths() == {"KMusic/Own/Alb/1.flac", "TMusic/ArtT/AT/2.flac",
                        "SoulSync/organized/ArtS/AS/3.flac", "Lidarr/ArtL/AL/4.flac"}


def test_admin_login_filtered_by_owner_name():
    http = _Http(PLAYLISTS, TRACKS, login={"token": "tok", "id": "u-admin", "username": "admin"})
    got = cpt.playlist_track_paths("http://nd", "admin", "pw", http=http, sleep=lambda s: None, owner="tee")
    assert got == {"TMusic/NotMine/x.flac"}


def test_pagination():
    many = [{"path": f"TMusic/a/{i}.flac"} for i in range(cpt.PAGE + 5)]
    got = cpt.playlist_track_paths("http://nd", "kim", "pw", http=_Http(PLAYLISTS[:1], {"p1": many}),
                                   sleep=lambda s: None)
    assert len(got) == cpt.PAGE + 5


# ── mapping ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("src,dest", [
    ("TMusic/ArtT/AT/2.flac", "KMusic/ArtT/AT/2.flac"),
    ("SoulSync/organized/ArtS/AS/3.flac", "KMusic/ArtS/AS/3.flac"),        # organized/ dropped
    ("SoulSync/other/ArtS/3.flac", "KMusic/other/ArtS/3.flac"),           # only organized/ is dropped
    ("Lidarr/ArtL/AL/4.flac", "KMusic/ArtL/AL/4.flac"),
    ("TMusic/organized/x.flac", "KMusic/organized/x.flac"),               # organized/ only under SoulSync
])
def test_dest_mapping(src, dest):
    assert cpt.dest_for(src, "KMusic") == dest


@pytest.mark.parametrize("src", ["KMusic/Own/1.flac", "/KMusic/Own/1.flac", "loose.flac", "TMusic/../../etc/passwd",
                                 "TMusic/a/../../../x"])
def test_dest_none_for_own_folder_unmappable_or_escaping(src):
    assert cpt.dest_for(src, "KMusic") is None


# ── run ──────────────────────────────────────────────────────────────────────

def _run(share, tmp_path, apply, prefix="KMusic", paths=None):
    manifest = tmp_path / "m.csv"
    totals = cpt.copy_tracks(paths or _paths(), prefix, str(share), str(manifest), apply=apply)
    with open(manifest, newline="") as f:
        return totals, list(csv.DictReader(f))


def test_dry_run_writes_manifest_and_no_files(share, tmp_path):
    totals, rows = _run(share, tmp_path, apply=False)
    assert {r["status"] for r in rows} == {"planned"}
    assert totals["count"] == 3 and totals["bytes"] == 10 + 3 + 4
    assert not (share / "KMusic" / "ArtT").exists()
    assert set(rows[0]) == {"source", "dest", "bytes", "status"}


def test_apply_copies_keeps_sources_and_is_idempotent(share, tmp_path):
    totals, rows = _run(share, tmp_path, apply=True)
    assert {r["status"] for r in rows} == {"copied"}
    assert (share / "KMusic/ArtT/AT/2.flac").read_bytes() == b"tt" * 5
    assert (share / "KMusic/ArtS/AS/3.flac").read_bytes() == b"sss"
    assert (share / "KMusic/ArtL/AL/4.flac").read_bytes() == b"llll"
    for rel in ("TMusic/ArtT/AT/2.flac", "SoulSync/organized/ArtS/AS/3.flac", "Lidarr/ArtL/AL/4.flac"):
        assert (share / rel).exists(), "sources are never moved or deleted"
    assert not list(share.rglob("*.part"))
    totals2, rows2 = _run(share, tmp_path, apply=True)
    assert {r["status"] for r in rows2} == {"already-present"}
    assert totals2["count"] == 0 and totals2["already_present"] == 3


def test_same_size_target_is_skipped_different_size_is_a_conflict_not_overwritten(share, tmp_path):
    (share / "KMusic/ArtT/AT").mkdir(parents=True)
    (share / "KMusic/ArtT/AT/2.flac").write_bytes(b"1234567890")      # same size (10)
    (share / "KMusic/ArtS/AS").mkdir(parents=True)
    (share / "KMusic/ArtS/AS/3.flac").write_bytes(b"different!")       # other size
    _, rows = _run(share, tmp_path, apply=True)
    by = {r["source"]: r["status"] for r in rows}
    assert by["TMusic/ArtT/AT/2.flac"] == "already-present"
    assert by["SoulSync/organized/ArtS/AS/3.flac"] == "conflict-exists"
    assert (share / "KMusic/ArtS/AS/3.flac").read_bytes() == b"different!"


def test_missing_source_is_reported_not_fatal(share, tmp_path):
    (share / "Lidarr/ArtL/AL/4.flac").unlink()
    totals, rows = _run(share, tmp_path, apply=True)
    assert {r["source"]: r["status"] for r in rows}["Lidarr/ArtL/AL/4.flac"] == "missing-source"
    assert totals["count"] == 2


def test_symlink_escaping_the_share_is_refused(share, tmp_path):
    outside = tmp_path / "outside.flac"
    outside.write_bytes(b"secret")
    link = share / "TMusic/ArtT/AT/link.flac"
    link.symlink_to(outside)
    totals, rows = _run(share, tmp_path, apply=True, paths={"TMusic/ArtT/AT/link.flac"})
    assert rows[0]["status"] == "refused"
    assert not (share / "KMusic/ArtT/AT/link.flac").exists()


# ── CLI ──────────────────────────────────────────────────────────────────────

class _FakeDb:
    def __init__(self, prefix="KMusic", login=("kim", "pw")):
        self.prefix, self.login = prefix, login

    def get_profile_library_prefix(self, pid):
        return self.prefix

    def get_profile_navidrome_login(self, pid):
        return self.login


def test_main_is_a_dry_run_by_default_and_uses_the_profiles_own_login(share, tmp_path, monkeypatch, capsys):
    http = _Http(PLAYLISTS, TRACKS)
    monkeypatch.setattr(cpt, "_navidrome_base_url", lambda: "http://nd")
    rc = cpt.main(["--profile-id", "2", "--share-root", str(share), "--manifest", str(tmp_path / "m.csv")],
                  db=_FakeDb(), http=http)
    assert rc == 0
    assert http.calls[0] == ("POST", "http://nd/auth/login", {"username": "kim", "password": "pw"})
    assert not (share / "KMusic/ArtT").exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "pw" not in out


def test_main_apply_copies(share, tmp_path, monkeypatch):
    monkeypatch.setattr(cpt, "_navidrome_base_url", lambda: "http://nd")
    assert cpt.main(["--profile-id", "2", "--apply", "--share-root", str(share),
                     "--manifest", str(tmp_path / "m.csv")], db=_FakeDb(), http=_Http(PLAYLISTS, TRACKS)) == 0
    assert (share / "KMusic/ArtT/AT/2.flac").exists()


@pytest.mark.parametrize("db", [_FakeDb(prefix=""), _FakeDb(login=None)])
def test_main_refuses_a_profile_without_a_folder_or_login(share, tmp_path, monkeypatch, db):
    monkeypatch.setattr(cpt, "_navidrome_base_url", lambda: "http://nd")
    assert cpt.main(["--profile-id", "2", "--share-root", str(share), "--manifest", str(tmp_path / "m.csv")],
                    db=db, http=_Http(PLAYLISTS, TRACKS)) == 2


def test_main_refuses_the_admin_profile(share, tmp_path):
    assert cpt.main(["--profile-id", "1", "--share-root", str(share)], db=_FakeDb(), http=_Http([], {})) == 2
