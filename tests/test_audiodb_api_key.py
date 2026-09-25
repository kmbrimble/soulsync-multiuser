"""Fork: TheAudioDB retired the public v1 test key "2" (every call 404s, verified
25 Sep 2026); the current public key is "123". The key is configurable."""
import core.audiodb_client as adb


class _Cfg:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "audiodb.api_key" else default


def test_default_key_is_the_current_public_key(monkeypatch):
    monkeypatch.setattr(adb, "_configured_api_key", lambda: None)
    assert adb.AudioDBClient().BASE_URL == "https://www.theaudiodb.com/api/v1/json/123"


def test_retired_key_is_not_the_default():
    assert not adb.AudioDBClient.BASE_URL.endswith("/json/2")


def test_configured_key_wins(monkeypatch):
    monkeypatch.setattr(adb, "_configured_api_key", lambda: "my-premium-key")
    assert adb.AudioDBClient().BASE_URL == "https://www.theaudiodb.com/api/v1/json/my-premium-key"


def test_configured_key_reads_settings(monkeypatch):
    import core.settings as settings
    monkeypatch.setattr(settings, "config_manager", _Cfg("  456  "))
    assert adb._configured_api_key() == "456"
    monkeypatch.setattr(settings, "config_manager", _Cfg(""))
    assert adb._configured_api_key() is None
