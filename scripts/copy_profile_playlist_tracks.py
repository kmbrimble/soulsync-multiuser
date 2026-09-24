#!/usr/bin/env python3
"""One-off (fork): copy the tracks in a profile's Navidrome playlists into that
profile's music folder, so they stop depending on other people's folders.

Reads the profile's playlists from the Navidrome native API (logged in as the
profile's own Navidrome user), and for every track outside the profile's folder
copies it to ``<share root>/<prefix>/<path minus its top folder>``
(``SoulSync/organized/`` is dropped as a whole). Copies use reflink when the
filesystem has it. Nothing is ever moved, deleted or overwritten.

Dry run by default. Run inside the SoulSync container:

    python scripts/copy_profile_playlist_tracks.py --profile-id 2            # dry run
    python scripts/copy_profile_playlist_tracks.py --profile-id 2 --apply

Every planned/done copy goes to a CSV manifest (source, dest, bytes, status).
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from typing import Iterable, Optional, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from core.navidrome_folder_map import PAGE, _get  # noqa: E402  (same paging + back-off as the folder map)
from core.profile_library_folder import _share_root, normalize_prefix  # noqa: E402


def _login(base: str, username: str, password: str, http):
    r = http.post(f"{base}/auth/login", json={"username": username, "password": password}, timeout=30)
    r.raise_for_status()
    body = r.json() or {}
    if not body.get("token"):
        raise RuntimeError("Navidrome native login returned no token")
    return body


def _pages(http, url, headers, sleep):
    start = 0
    while True:
        rows = _get(http, url, sleep=sleep, headers=headers, params={"_start": start, "_end": start + PAGE}).json() or []
        yield from rows
        if len(rows) < PAGE:
            return
        start += PAGE


def playlist_track_paths(base_url: str, username: str, password: str, *, http=requests,
                         sleep=time.sleep, owner: Optional[str] = None) -> Set[str]:
    """Library-relative paths of every track in the user's playlists. With
    ``owner`` (admin login) only that owner's playlists; otherwise the login's own."""
    base = base_url.rstrip("/")
    body = _login(base, username, password, http)
    headers = {"X-ND-Authorization": f"Bearer {body['token']}"}
    if owner:
        mine = lambda p: p.get("ownerName") == owner  # noqa: E731
    else:
        mine = lambda p: p.get("ownerId") == body.get("id") or p.get("ownerName") == body.get("username", username)  # noqa: E731
    out: Set[str] = set()
    for pl in _pages(http, f"{base}/api/playlist", headers, sleep):
        if not mine(pl):
            continue
        for t in _pages(http, f"{base}/api/playlist/{pl['id']}/tracks", headers, sleep):
            if t.get("path"):
                out.add(str(t["path"]).replace("\\", "/").lstrip("/"))
    return out


def dest_for(src: str, prefix: str) -> Optional[str]:
    """Share-relative destination for ``src``, or None when it is already in the
    folder, has no top folder to drop, or tries to leave the share."""
    parts = [p for p in src.replace("\\", "/").split("/") if p]
    prefix = normalize_prefix(prefix)
    if ".." in parts or len(parts) < 2 or parts[0] == prefix:
        return None
    rest = parts[1:]
    if parts[0] == "SoulSync" and rest[0] == "organized" and len(rest) > 1:
        rest = rest[1:]
    return "/".join([prefix, *rest])


def _inside(root: str, path: str) -> bool:
    real = os.path.realpath(path)
    return real == root or real.startswith(root + os.sep)


def _copy(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    try:
        subprocess.run(["cp", "--reflink=auto", "--preserve=timestamps", "--", src, part],
                       check=True, capture_output=True)
        os.replace(part, dest)
    finally:
        if os.path.exists(part):
            os.unlink(part)


def copy_tracks(paths: Iterable[str], prefix: str, share_root: str, manifest_path: str, *, apply: bool) -> dict:
    """Plan (and with ``apply`` do) the copies; write the manifest; return totals."""
    root = os.path.realpath(share_root)
    totals = {"count": 0, "bytes": 0, "already_present": 0, "problems": 0}
    with open(manifest_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "dest", "bytes", "status"])
        for src in sorted(set(paths)):
            rel = dest_for(src, prefix)
            if rel is None:
                continue
            s, d = os.path.join(root, src.lstrip("/")), os.path.join(root, rel)
            size, status = 0, "planned" if not apply else "copied"
            if not os.path.isfile(s):
                status = "missing-source"
            elif not _inside(root, s) or not _inside(root, os.path.dirname(d)):
                status = "refused"
            else:
                size = os.path.getsize(s)
                if os.path.exists(d):
                    status = "already-present" if os.path.getsize(d) == size else "conflict-exists"
                elif apply:
                    try:
                        _copy(s, d)
                    except (OSError, subprocess.CalledProcessError):
                        status = "failed"
            w.writerow([src, rel, size, status])
            if status in ("planned", "copied"):
                totals["count"] += 1
                totals["bytes"] += size
            elif status == "already-present":
                totals["already_present"] += 1
            else:
                totals["problems"] += 1
    return totals


def _navidrome_base_url() -> str:
    from core.settings import config_manager
    return config_manager.get_navidrome_config().get("base_url") or ""


def main(argv=None, *, db=None, http=requests) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--profile-id", type=int, required=True)
    ap.add_argument("--apply", action="store_true", help="really copy (default: dry run)")
    ap.add_argument("--share-root", help="music share root as this container sees it (default: from config)")
    ap.add_argument("--manifest", help="CSV path (default: /tmp/copy_profile_<id>_<time>.csv)")
    ap.add_argument("--owner", help="Navidrome user whose playlists to read, using the admin login "
                                    "(only when the profile has no Navidrome login of its own)")
    a = ap.parse_args(argv)

    if a.profile_id == 1:
        print("refusing: the admin profile has no folder of its own")
        return 2
    if db is None:
        from database.music_database import get_database
        db = get_database()
    prefix = normalize_prefix(db.get_profile_library_prefix(a.profile_id))
    if not prefix:
        print(f"refusing: profile {a.profile_id} has no library folder set")
        return 2
    share_root = a.share_root or _share_root(prefix)
    if not share_root:
        print("refusing: cannot derive the music share root from config; pass --share-root")
        return 2
    base = _navidrome_base_url()
    login = db.get_profile_navidrome_login(a.profile_id)
    owner = None
    if not login:
        if not a.owner:
            print(f"refusing: profile {a.profile_id} has no Navidrome login; pass --owner NAME to read "
                  "that user's playlists with the admin login")
            return 2
        from core.settings import config_manager
        cfg = config_manager.get_navidrome_config()
        login, owner = (cfg.get("username"), cfg.get("password")), a.owner
    if not (base and login[0] and login[1]):
        print("refusing: Navidrome is not configured")
        return 2

    paths = playlist_track_paths(base, login[0], login[1], http=http, owner=owner)
    manifest = a.manifest or f"/tmp/copy_profile_{a.profile_id}_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    t = copy_tracks(paths, prefix, share_root, manifest, apply=a.apply)
    print(f"{'APPLY' if a.apply else 'DRY RUN (nothing copied)'}: profile {a.profile_id} -> {prefix}/  "
          f"({len(paths)} playlist tracks)")
    print(f"{'copied' if a.apply else 'to copy'}: {t['count']} files, {t['bytes']} bytes; "
          f"already present: {t['already_present']}; problems (missing/conflict/refused/failed): {t['problems']}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
