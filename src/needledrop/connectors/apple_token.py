"""Apple Music developer-token (ES256 JWT) signing."""

from __future__ import annotations

import time

import jwt

# Apple allows up to ~6 months; use 180 days, comfortably under the cap.
DEVELOPER_TOKEN_TTL = 180 * 24 * 60 * 60


def make_developer_token(
    p8_pem: str, *, team_id: str, key_id: str, now: int | None = None, ttl: int = DEVELOPER_TOKEN_TTL
) -> str:
    """Sign an Apple Music developer token from a MusicKit .p8 (PKCS#8 EC P-256) key."""
    issued = int(now if now is not None else time.time())
    payload = {"iss": team_id, "iat": issued, "exp": issued + ttl}
    return jwt.encode(payload, p8_pem, algorithm="ES256", headers={"kid": key_id})
