"""Apple Music developer-token (ES256 JWT) signing."""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from needledrop.keystore import get_backend

# Apple allows up to ~6 months; use 180 days, comfortably under the cap.
DEVELOPER_TOKEN_TTL = 180 * 24 * 60 * 60


def make_developer_token(
    p8_pem: str,
    *,
    team_id: str,
    key_id: str,
    now: int | None = None,
    ttl: int = DEVELOPER_TOKEN_TTL,
) -> str:
    """Sign an Apple Music developer token from a MusicKit .p8 (PKCS#8 EC P-256) key."""
    issued = int(now if now is not None else time.time())
    payload = {"iss": team_id, "iat": issued, "exp": issued + ttl}
    return jwt.encode(payload, p8_pem, algorithm="ES256", headers={"kid": key_id})


KEY_TEAM_ID = "apple_team_id"
KEY_KEY_ID = "apple_key_id"
KEY_P8 = "apple_p8_private_key"
KEY_USER_TOKEN = "apple_music_user_token"


@dataclass(frozen=True)
class AppleCredentials:
    team_id: str
    key_id: str
    p8_pem: str
    user_token: str | None = None


def store_developer_credentials(*, team_id: str, key_id: str, p8_pem: str) -> None:
    backend = get_backend()
    backend.set(KEY_TEAM_ID, team_id)
    backend.set(KEY_KEY_ID, key_id)
    backend.set(KEY_P8, p8_pem)


def store_user_token(token: str) -> None:
    get_backend().set(KEY_USER_TOKEN, token)


def load_credentials() -> AppleCredentials:
    backend = get_backend()
    team_id = backend.get(KEY_TEAM_ID)
    key_id = backend.get(KEY_KEY_ID)
    p8_pem = backend.get(KEY_P8)
    if not (team_id and key_id and p8_pem):
        raise RuntimeError(
            "Apple developer credentials are not configured. Run "
            "`needledrop auth apple set-credentials` first."
        )
    return AppleCredentials(
        team_id=team_id, key_id=key_id, p8_pem=p8_pem, user_token=backend.get(KEY_USER_TOKEN)
    )
