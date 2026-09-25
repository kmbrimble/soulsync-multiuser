"""Where each Navidrome track really lives (fork).

SoulSync's ``tracks.file_path`` for Navidrome is the Subsonic *fake* path
(``Artist/Album/NN - Title.ext``): the top-level share folder (``KMusic/``) is
not in it. Navidrome's native REST API reports the real library-relative path,
so it is mirrored into ``track_library_folder`` (track id -> relative path) and
only the Library page's folder scoping reads it. ``file_path`` is untouched.

Fail soft: any error leaves the previous mapping in place. Credentials and
tokens are never logged.
"""

from __future__ import annotations

import posixpath
import threading
import time
from typing import Callable, Dict, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("navidrome_folder_map")

PAGE = 1000
MAX_PAGES = 2000          # ~2M songs; a runaway guard, not a limit anyone hits
RETRIES = 3
_lock = threading.Lock()


def _get(http, url, *, retries=RETRIES, sleep: Callable = time.sleep, **kw):
    """GET with back-off on 429/5xx/connection errors."""
    for attempt in range(retries + 1):
        try:
            r = http.get(url, timeout=60, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.exceptions.RequestException(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException:
            if attempt == retries:
                raise
            sleep(2 ** attempt)


def _norm(p) -> str:
    return str(p).replace('\\', '/')


def _relative_to_common_root(found) -> Dict[str, str]:
    """Navidrome's ``path`` is relative to the song's library. Re-root every path
    at the common directory of all library paths seen, so ``/music/KMusic`` +
    ``Artist/x.mp3`` -> ``KMusic/Artist/x.mp3``. One library (root == its own
    path) or no ``libraryPath`` (older Navidrome) leaves ``path`` as it was."""
    libs = {lib for _, lib, _ in found if lib}
    try:
        root = posixpath.commonpath(libs) if len(libs) > 1 else None
    except ValueError:      # mixed absolute/relative library paths: don't guess
        root = None
    out: Dict[str, str] = {}
    for sid, lib, path in found:
        out[sid] = posixpath.relpath(posixpath.join(lib, path), root) if root and lib else path
    return out


def fetch_song_paths(base_url: str, username: str, password: str, *, http=requests,
                     sleep: Callable = time.sleep) -> Dict[str, str]:
    """{navidrome song id: library-relative path} for every song, via the native API."""
    base = base_url.rstrip('/')
    login = http.post(f"{base}/auth/login", json={'username': username, 'password': password}, timeout=30)
    login.raise_for_status()
    token = (login.json() or {}).get('token')
    if not token:
        raise RuntimeError("Navidrome native login returned no token")
    headers = {'X-ND-Authorization': f'Bearer {token}'}

    found = []   # (id, library path or None, path); rel needs every library seen, so resolve at the end
    for page in range(MAX_PAGES):
        start = page * PAGE
        r = _get(http, f"{base}/api/song", sleep=sleep, headers=headers,
                 params={'_start': start, '_end': start + PAGE, '_sort': 'id', '_order': 'ASC'})
        rows = r.json() or []
        for song in rows:
            sid, path = song.get('id'), song.get('path')
            if sid and path:
                lib = song.get('libraryPath')
                found.append((str(sid), _norm(lib).rstrip('/') if lib else None, _norm(path).lstrip('/')))
        if len(rows) < PAGE:
            return _relative_to_common_root(found)
    raise RuntimeError("Navidrome song listing did not terminate")


def refresh_track_folders(database=None, *, http=requests, sleep: Callable = time.sleep) -> Optional[int]:
    """Rebuild track_library_folder from Navidrome. Returns the row count, or
    None when skipped/failed (the old mapping is kept)."""
    from core.settings import config_manager
    if not _lock.acquire(blocking=False):
        return None
    try:
        if config_manager.get_active_media_server() != 'navidrome':
            return None
        if database is None:
            from database.music_database import get_database
            database = get_database()
        cfg = config_manager.get_navidrome_config()
        if not (cfg.get('base_url') and cfg.get('username') and cfg.get('password')):
            logger.warning("track folder map: Navidrome credentials not configured")
            return None
        paths = fetch_song_paths(cfg['base_url'], cfg['username'], cfg['password'], http=http, sleep=sleep)
        if not paths:
            logger.warning("track folder map: Navidrome returned no songs; keeping the old mapping")
            return None
        count = database.replace_track_folders(paths)
        logger.info("track folder map: %d tracks mapped", count)
        return count
    except Exception as e:  # noqa: BLE001 - fail soft; never include credentials
        logger.warning("track folder map refresh failed (%s); keeping the old mapping", type(e).__name__)
        return None
    finally:
        _lock.release()


def refresh_in_background(database=None) -> None:
    threading.Thread(target=refresh_track_folders, args=(database,), daemon=True,
                     name="track-folder-map").start()
