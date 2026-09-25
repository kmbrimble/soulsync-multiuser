"""Wishlist track selection helpers."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from core.wishlist.classification import classify_wishlist_track
from core.wishlist.payloads import sanitize_track_data_for_processing


def _owner_keyer() -> Callable[[dict[str, Any]], Any]:
    """fork: dedupe owner of a track — its profile only when that profile has its own
    output folder (it needs its own copy); None for admin/folderless profiles, which
    share one library and keep upstream's one-download-per-track behaviour."""
    cache: dict[Any, Any] = {}

    def owner(track: dict[str, Any]) -> Any:
        pid = track.get('profile_id')
        if not pid:
            return None
        if pid not in cache:
            try:
                from core.imports.paths import library_root_for_profile
                cache[pid] = pid if library_root_for_profile(pid) else None
            except Exception:  # noqa: BLE001 - unresolvable => shared library, as upstream
                cache[pid] = None
        return cache[pid]

    return owner


def sanitize_and_dedupe_wishlist_tracks(
    raw_tracks: Iterable[dict[str, Any]],
    *,
    sanitizer: Callable[[dict[str, Any]], dict[str, Any]] = sanitize_track_data_for_processing,
) -> tuple[list[dict[str, Any]], int]:
    """Sanitize wishlist tracks and drop duplicate track IDs."""
    sanitized_tracks: list[dict[str, Any]] = []
    seen_track_ids: set[Any] = set()
    duplicates_found = 0
    owner_of = _owner_keyer()

    for track in raw_tracks:
        sanitized_track = sanitizer(track)
        spotify_track_id = (
            sanitized_track.get('track_id')
            or sanitized_track.get('spotify_track_id')
            or sanitized_track.get('id')
        )

        # fork: the same track wanted by two profiles is two downloads (each
        # profile gets its own copy in its own folder)
        dedupe_key = (owner_of(sanitized_track), spotify_track_id)

        if spotify_track_id and dedupe_key in seen_track_ids:
            duplicates_found += 1
            continue

        sanitized_tracks.append(sanitized_track)
        if spotify_track_id:
            seen_track_ids.add(dedupe_key)

    return sanitized_tracks, duplicates_found


def filter_wishlist_tracks_by_category(
    tracks: Iterable[dict[str, Any]],
    category: str,
    *,
    classifier: Callable[[dict[str, Any]], str] = classify_wishlist_track,
) -> tuple[list[dict[str, Any]], int]:
    """Filter wishlist tracks by category and return the matches plus total count."""
    filtered_tracks: list[dict[str, Any]] = []
    seen_track_ids: set[Any] = set()
    owner_of = _owner_keyer()

    for track in tracks:
        track_category = classifier(track)
        spotify_track_id = track.get('track_id') or track.get('spotify_track_id') or track.get('id')
        if category != track_category:
            continue

        if spotify_track_id:
            dedupe_key = (owner_of(track), spotify_track_id)
            if dedupe_key in seen_track_ids:
                continue
            seen_track_ids.add(dedupe_key)

        filtered_tracks.append(track)

    total_in_category = sum(1 for track in tracks if classifier(track) == category)
    return filtered_tracks, total_in_category


def prepare_wishlist_tracks_for_display(
    raw_tracks: Iterable[dict[str, Any]],
    *,
    category: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Sanitize, dedupe, and optionally filter wishlist tracks for API output."""
    sanitized_tracks, duplicates_found = sanitize_and_dedupe_wishlist_tracks(raw_tracks)

    result_tracks = sanitized_tracks
    total = len(sanitized_tracks)

    if category:
        result_tracks, total = filter_wishlist_tracks_by_category(sanitized_tracks, category)

    if limit is not None:
        result_tracks = result_tracks[:limit]

    return {
        'tracks': result_tracks,
        'total': total,
        'duplicates_found': duplicates_found,
        'category': category,
    }


__all__ = [
    "sanitize_and_dedupe_wishlist_tracks",
    "filter_wishlist_tracks_by_category",
    "prepare_wishlist_tracks_for_display",
]
