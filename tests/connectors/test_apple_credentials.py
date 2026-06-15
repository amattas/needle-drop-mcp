import pytest

import needledrop.keystore as keystore
from needledrop.connectors.apple_token import (
    AppleCredentials,
    load_credentials,
    store_developer_credentials,
    store_user_token,
)


class InMemoryBackend:
    def __init__(self):
        self._d = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, key):
        self._d.pop(key, None)


def _use_memory_backend():
    original = keystore.get_backend()
    keystore.set_backend(InMemoryBackend())
    return original


def test_store_and_load_roundtrip():
    original = _use_memory_backend()
    try:
        store_developer_credentials(team_id="T1", key_id="K1", p8_pem="PEMDATA")
        store_user_token("user-tok")
        creds = load_credentials()
        assert creds == AppleCredentials(
            team_id="T1", key_id="K1", p8_pem="PEMDATA", user_token="user-tok"
        )
    finally:
        keystore.set_backend(original)


def test_load_without_developer_credentials_raises():
    original = _use_memory_backend()
    try:
        with pytest.raises(RuntimeError) as exc:
            load_credentials()
        assert "set-credentials" in str(exc.value)
    finally:
        keystore.set_backend(original)


def test_user_token_optional():
    original = _use_memory_backend()
    try:
        store_developer_credentials(team_id="T1", key_id="K1", p8_pem="PEM")
        creds = load_credentials()
        assert creds.user_token is None
    finally:
        keystore.set_backend(original)
