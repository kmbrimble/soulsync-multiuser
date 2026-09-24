"""Per-profile Deezer ARL client resolver (fork feature).

Modelled on ``get_tidal_client_for_profile``. Lives in core/ so api/* blueprints
can import it without importing web_server (circular).

Admin (profile 1) and profiles without an ARL of their own get the GLOBAL
``deezer_dl`` client, exactly as upstream. A profile with its own ARL gets a
dedicated client; the global client and config are never touched.
"""

from __future__ import annotations

import threading

from core.profile_context import get_current_profile_id
from database.music_database import get_database
from utils.logging_config import get_logger

logger = get_logger("profile_deezer")

_clients: dict = {}   # profile_id -> (arl, client)
_lock = threading.Lock()


def resolve_deezer_dl_client(global_client, profile_id=None):
    """The Deezer client whose account the profile should see.

    Returns None (never the global client) when a non-admin profile's row can't
    be read: falling back would show them the ARL owner's private playlists."""
    if profile_id is None:
        profile_id = get_current_profile_id()
    if not profile_id or profile_id == 1:
        return global_client
    try:
        arl = get_database().get_profile_deezer_arl(profile_id)
    except Exception as e:  # noqa: BLE001
        logger.error("could not read Deezer ARL for profile %s: %s", profile_id, e)
        return None
    if not arl:
        clear_profile_deezer_client(profile_id)
        return global_client
    with _lock:
        cached = _clients.get(profile_id)
        if cached is not None and cached[0] == arl:
            return cached[1]
        from core.deezer_download_client import DeezerDownloadClient
        # ponytail: login runs under the lock (a few profiles, rare); per-profile locks if that grows
        client = DeezerDownloadClient(arl=arl)
        _clients[profile_id] = (arl, client)
        return client


def clear_profile_deezer_client(profile_id) -> None:
    """Evict a profile's cached client (after the ARL changes or is removed)."""
    with _lock:
        _clients.pop(profile_id, None)


def verify_arl(arl: str):
    """Live login with ``arl``. Returns ``(ok, display_name)``; never logs the ARL."""
    from core.deezer_download_client import DeezerDownloadClient
    client = DeezerDownloadClient(arl=arl)
    if not client.is_authenticated():
        return False, None
    return True, (client._user_data or {}).get('BLOG_NAME') or 'Deezer user'
