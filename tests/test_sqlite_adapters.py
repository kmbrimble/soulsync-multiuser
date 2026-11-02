"""Binding a datetime/date to sqlite must keep storing exactly what Python's (now deprecated)
default adapter stored, without raising the Python 3.12+ DeprecationWarning."""
import datetime as dt
import sqlite3
import warnings

import pytest

import database  # noqa: F401  (importing the package registers the adapters)

# Literal strings recorded from CPython 3.14.7's built-in default adapter before it was replaced.
CASES = [
    (dt.datetime(2026, 11, 2, 10, 5, 7), "2026-11-02 10:05:07"),
    (dt.datetime(2026, 11, 2, 10, 5, 7, 123456), "2026-11-02 10:05:07.123456"),
    (dt.datetime(2026, 11, 2, 10, 5, 7, tzinfo=dt.timezone.utc), "2026-11-02 10:05:07+00:00"),
    (dt.datetime(2026, 11, 2, 10, 5, 7, 5, tzinfo=dt.timezone(dt.timedelta(hours=10))),
     "2026-11-02 10:05:07.000005+10:00"),
    (dt.date(2026, 11, 2), "2026-11-02"),
]


@pytest.mark.parametrize("value,text", CASES)
def test_bound_value_is_stored_as_the_old_default_text_without_a_warning(value, text):
    conn = sqlite3.connect(":memory:")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any DeprecationWarning fails the test
            assert conn.execute("select ?", (value,)).fetchone()[0] == text
    finally:
        conn.close()


def test_bound_datetimes_still_order_against_sqlite_timestamp_text():
    # Rows written with CURRENT_TIMESTAMP use 'YYYY-MM-DD HH:MM:SS'; a bound datetime must compare with them.
    conn = sqlite3.connect(":memory:")
    try:
        later = dt.datetime(2026, 11, 2, 10, 5, 8)
        earlier = dt.datetime(2026, 11, 2, 10, 5, 6)
        assert conn.execute("select ? > '2026-11-02 10:05:07'", (later,)).fetchone()[0] == 1
        assert conn.execute("select ? < '2026-11-02 10:05:07'", (earlier,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_registering_twice_is_harmless():
    from database import sqlite_adapters
    sqlite_adapters.register()
    sqlite_adapters.register()
    conn = sqlite3.connect(":memory:")
    try:
        assert conn.execute("select ?", (dt.date(2026, 11, 2),)).fetchone()[0] == "2026-11-02"
    finally:
        conn.close()
