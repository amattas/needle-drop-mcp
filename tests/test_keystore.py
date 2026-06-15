import needledrop.keystore as keystore_mod
from needledrop.keystore import KeyringBackend, get_backend, set_backend


class InMemoryBackend:
    def __init__(self):
        self._store = {}

    def get(self, key):
        return self._store.get(key)

    def set(self, key, value):
        self._store[key] = value

    def delete(self, key):
        self._store.pop(key, None)


def test_pluggable_backend_roundtrip():
    original = get_backend()
    try:
        fake = InMemoryBackend()
        set_backend(fake)
        assert get_backend() is fake
        get_backend().set("apple_user_token", "abc123")
        assert get_backend().get("apple_user_token") == "abc123"
        get_backend().delete("apple_user_token")
        assert get_backend().get("apple_user_token") is None
    finally:
        set_backend(original)


def test_keyring_backend_uses_keyring(monkeypatch):
    calls = {}

    def fake_set(service, key, value):
        calls[(service, key)] = value

    def fake_get(service, key):
        return calls.get((service, key))

    monkeypatch.setattr(keystore_mod.keyring, "set_password", fake_set)
    monkeypatch.setattr(keystore_mod.keyring, "get_password", fake_get)

    backend = KeyringBackend(service_name="needledrop-test")
    backend.set("team_id", "TEAM123")
    assert backend.get("team_id") == "TEAM123"
    assert calls[("needledrop-test", "team_id")] == "TEAM123"
