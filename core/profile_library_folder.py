"""A profile can be limited to one top-level folder of the shared music library
(``profiles.library_path_prefix``, e.g. ``KMusic``): its Library page then shows
only tracks stored under that folder. Empty = everything (upstream behaviour);
the admin profile is never limited.

This is a *view* filter for the Library page queries only. It is deliberately
not part of ``core.library_scope`` (which sync and matching also read): playlist
sync, matching and the wishlist keep seeing the whole library.
"""

from __future__ import annotations

from typing import List, Tuple


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
