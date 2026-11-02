"""Explicit sqlite3 adapters for datetime and date values.

Python 3.12 deprecated the adapters sqlite3 used implicitly when a ``datetime`` or
``date`` is bound as a query parameter. The code base binds datetimes in about thirty
places (the enrichment workers, MusicBrainz, the watchlist...), so instead of editing
each call site this registers, once, exactly what the built-in default produced:

- ``datetime`` -> ``value.isoformat(" ")``  e.g. ``2026-11-02 10:05:07`` / ``...07.123456+10:00``
- ``date``     -> ``value.isoformat()``     e.g. ``2026-11-02``

That is deliberately NOT the ``isoformat()`` (``T`` separator) recipe in the sqlite3 docs:
existing rows hold the space form, and a different separator would sort differently
against them. ``tests/test_sqlite_adapters.py`` pins the stored text.

Registration is process-global and idempotent. It happens when the ``database`` package
is first imported (see ``database/__init__.py``).
"""

import datetime
import sqlite3


def _adapt_datetime(value: datetime.datetime) -> str:
    return value.isoformat(" ")


def _adapt_date(value: datetime.date) -> str:
    return value.isoformat()


def register() -> None:
    sqlite3.register_adapter(datetime.datetime, _adapt_datetime)
    sqlite3.register_adapter(datetime.date, _adapt_date)


register()
