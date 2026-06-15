"""Pluggable secret storage. Default backend is the OS keyring; a 1Password
backend (or any object satisfying SecretBackend) can be swapped in via set_backend.
Named `keystore` (not `secrets`) to avoid shadowing the stdlib `secrets` module.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import keyring

SERVICE_NAME = "needledrop"


@runtime_checkable
class SecretBackend(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringBackend:
    """Stores secrets in the OS keychain via the `keyring` library."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self._service = service_name

    def get(self, key: str) -> str | None:
        return keyring.get_password(self._service, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(self._service, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(self._service, key)
        except keyring.errors.PasswordDeleteError:
            pass


_backend: SecretBackend = KeyringBackend()


def get_backend() -> SecretBackend:
    return _backend


def set_backend(backend: SecretBackend) -> None:
    global _backend
    _backend = backend
