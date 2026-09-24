"""A profile can be limited to one top-level folder of the shared music library
(``profiles.library_path_prefix``, e.g. ``KMusic``): its Library page then shows
only tracks stored under that folder. Empty = everything (upstream behaviour);
the admin profile is never limited.

The same folder also scopes playlist matching ("is this track already in *my*
library?", see ``MusicDatabase._profile_folder_sql``) and is where that profile's
downloads are organised (``folder_root_for_profile``, read by
``core.imports.paths.library_root_for_profile``). It is deliberately not part of
``core.library_scope`` (the plex/jellyfin own-library mechanism).
"""

from __future__ import annotations

import os
from typing import List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("profile_library_folder")


def normalize_prefix(raw) -> str:
    """'  KMusic/ ' -> 'KMusic'. Case is kept as given (matched as stored)."""
    p = str(raw or '').strip().replace('\\', '/')
    return p.rstrip('/') if p.strip('/') else ''


def track_path_sql(prefix: str, column: str = 't.file_path') -> Tuple[str, List[str]]:
    """WHERE fragment: ``column`` lies under folder ``prefix`` on a path-segment
    boundary (``KMusic`` matches ``KMusic/x`` and ``/KMusic/x``, not ``KMusicOld/x``).
    substr() compares case-sensitively and needs no LIKE escaping."""
    prefix = normalize_prefix(prefix)
    if not prefix:
        return '1=1', []
    variants = [prefix] if prefix.startswith('/') else [prefix, '/' + prefix]
    clauses, params = [], []
    for v in variants:
        v = v + '/'
        clauses.append(f"substr({column}, 1, {len(v)}) = ?")
        params.append(v)
    return '(' + ' OR '.join(clauses) + ')', params


def _share_root(prefix: str) -> Optional[str]:
    """The music share root, derived from config: the nearest ancestor of the
    transfer folder that already holds ``prefix``, else a ``library.music_paths``
    entry that contains the transfer folder."""
    from core.imports.paths import _get_config_manager, config_root_path
    cfg = _get_config_manager()
    transfer = config_root_path(cfg.get("soulseek.transfer_path", "./Transfer"), "./Transfer")
    d = transfer
    while os.path.dirname(d) != d:
        d = os.path.dirname(d)
        if os.path.isdir(os.path.join(d, prefix)):
            return d
    music_paths = cfg.get("library.music_paths", []) or []
    for p in [music_paths] if isinstance(music_paths, str) else music_paths:
        root = config_root_path(p)
        if root and transfer.startswith(root.rstrip("/") + "/"):
            return root
    return None


def folder_root_for_profile(profile_id, db=None) -> Optional[str]:
    """``<share root>/<prefix>`` where a folder-limited profile's downloads go, or
    None (admin, no folder, or no share root derivable: the shared transfer folder)."""
    if not profile_id or int(profile_id) == 1:
        return None
    try:
        if db is None:
            from database.music_database import get_database
            db = get_database()
        prefix = normalize_prefix(db.get_profile_library_prefix(int(profile_id))).strip("/")
    except Exception as exc:  # noqa: BLE001 - no folder known: shared folder
        logger.debug("profile folder lookup failed for %s: %s", profile_id, exc)
        return None
    if not prefix or ".." in prefix.split("/"):
        return None
    root = _share_root(prefix)
    if not root:
        logger.warning("profile %s has folder %r but the music share root cannot be derived "
                       "from config; downloads stay in the shared folder", profile_id, prefix)
        return None
    return os.path.join(root, prefix)
