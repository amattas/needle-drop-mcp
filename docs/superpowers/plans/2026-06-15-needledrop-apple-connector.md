# NeedleDrop Apple Music Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Apple Music connector — developer-token (ES256 JWT) signing, the read-only API client (storefront, paginated library albums/songs/playlists, catalog search), and the `needledrop auth apple` MusicKit-JS authorization helper that captures the Music User Token into the keystore.

**Architecture:** A `connectors/` package with one clear responsibility per module: `base.py` (abstract `MusicConnector` interface), `apple_token.py` (developer-token signing + keystore-backed credentials), `apple_models.py` (provider-shaped Pydantic models that parse Apple's JSON), `apple_music.py` (the read-only HTTP client returning those models), and `apple_auth.py` (the local MusicKit-JS page + token capture). Mutations are deferred to Plan 6. Pure logic (token signing, model parsing, page rendering, token extraction) is unit-tested; the HTTP client is tested with `httpx.MockTransport` + fixture JSON modeled on Apple's documented shapes (we have no live credentials to record cassettes against); the actual browser MusicKit flow and live API are documented manual paths.

**Tech Stack:** Python 3.13, `pyjwt[crypto]` (ES256), `httpx`, Pydantic v2, stdlib `http.server`/`webbrowser`/`threading`, `typer`. Builds on merged Plans 1–2 (`config`, `keystore`, `db`, `models`, `cli`).

**Plan series:** Plan 3 of 6 (design spec: `docs/superpowers/specs/2026-06-15-needledrop-mcp-design.md`, §6.1, §6.2, decision 4). Read paths only — `add_album`/`remove_album`/playlist mutations are Plan 6.

---

## Environment notes for implementers

- Python via the project env interpreter: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python` (NOT `mamba run`). Tests e.g.: `... -m pytest tests/connectors/test_apple_token.py -v`.
- After adding the `pyjwt[crypto]` dependency (Task 2) or any new import, reinstall editable: `... -m pip install --no-cache-dir -e ".[dev]"`.
- CI-parity gate before "done": `... -m pytest` green AND `... -m ruff check .` clean. (No Docker needed in this plan; the integration marker from Plan 2 still exists — the default `pytest` already runs everything here since none of these tests are marked `integration`.)

## Verified facts this plan relies on (from research; sources in the spec)

- **Developer token:** ES256 JWT, header `{alg: ES256, kid: <Key ID>}`, payload `{iss: <Team ID>, iat, exp}` (max ~6 months). The `.p8` is a PKCS#8 PEM EC P-256 key; `jwt.encode(payload, p8_pem, algorithm="ES256", headers={"kid": key_id})` (PyJWT ≥2 returns `str`). Sent as `Authorization: Bearer <token>`.
- **Music User Token:** header `Music-User-Token`; required for all `/v1/me/*`; catalog endpoints need only the developer token.
- **Library:** `GET /v1/me/library/{albums,songs,playlists}`, `limit` ≤ 100, response `{data:[{id, type, attributes:{...}}], next: "<path?offset=..>", meta:{total}}`; follow `next` until absent. Library ids look like `l.XXXX`. Library resources do NOT carry ISRC/UPC.
- **Storefront:** `GET /v1/me/storefront` → `data[0].id` (e.g. `us`); required in catalog paths.
- **Catalog search:** `GET /v1/catalog/{storefront}/search?term=&types=albums,songs&limit=` (limit ≤ 25); response `{results:{albums:{data:[...]}, songs:{data:[...]}}}`. Catalog album attributes include `upc`; catalog song attributes include `isrc`.
- **MusicKit JS v3:** `<script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js">`; on the `musickitloaded` event call `MusicKit.configure({developerToken, app:{name, build}})` (sync) then `await MusicKit.getInstance().authorize()` → the Music User Token string.

---

## File Structure

```text
src/needledrop/connectors/
├── __init__.py
├── base.py            # abstract MusicConnector (read interface)
├── apple_token.py     # developer-token ES256 signing + keystore credentials
├── apple_models.py    # provider Pydantic models (library + catalog) with from_api
├── apple_music.py     # read-only Apple Music HTTP client
└── apple_auth.py      # MusicKit-JS local auth helper (page + token capture)

src/needledrop/cli.py  # MODIFY: add `auth apple set-credentials` / `auth apple login`
pyproject.toml         # MODIFY: add pyjwt[crypto] dependency

tests/connectors/
├── test_base.py
├── test_apple_token.py
├── test_apple_credentials.py
├── test_apple_models.py
├── test_apple_music.py
└── test_apple_auth.py
tests/test_cli_auth.py
```

---

### Task 1: Abstract connector interface

**Files:**
- Create: `src/needledrop/connectors/__init__.py`
- Create: `src/needledrop/connectors/base.py`
- Test: `tests/connectors/test_base.py`

- [ ] **Step 1: Write the failing test**

`tests/connectors/test_base.py`:

```python
import pytest

from needledrop.connectors.base import MusicConnector


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        MusicConnector()


def test_concrete_subclass_must_implement_all():
    class Incomplete(MusicConnector):
        def get_storefront(self):
            return "us"

    with pytest.raises(TypeError):
        Incomplete()


def test_full_subclass_instantiates():
    class Stub(MusicConnector):
        def get_storefront(self):
            return "us"

        def iter_library_albums(self):
            return iter(())

        def iter_library_songs(self):
            return iter(())

        def iter_library_playlists(self):
            return iter(())

        def search_catalog(self, storefront, term, types=("albums", "songs"), limit=25):
            return None

    stub = Stub()
    assert stub.get_storefront() == "us"
    assert list(stub.iter_library_albums()) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.connectors'`.

- [ ] **Step 3: Implement**

`src/needledrop/connectors/__init__.py`: empty file.

`src/needledrop/connectors/base.py`:

```python
"""Abstract connector interface every music-service connector implements.

Read-only for now; mutating operations (add/remove album, playlists) are added
in a later plan.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class MusicConnector(ABC):
    @abstractmethod
    def get_storefront(self) -> str:
        """Return the user's storefront code (e.g. 'us')."""

    @abstractmethod
    def iter_library_albums(self) -> Iterator[Any]:
        """Yield the user's saved library albums."""

    @abstractmethod
    def iter_library_songs(self) -> Iterator[Any]:
        """Yield the user's saved library songs."""

    @abstractmethod
    def iter_library_playlists(self) -> Iterator[Any]:
        """Yield the user's library playlists."""

    @abstractmethod
    def search_catalog(
        self, storefront: str, term: str, types: tuple[str, ...] = ("albums", "songs"), limit: int = 25
    ) -> Any:
        """Search the provider catalog."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_base.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/__init__.py src/needledrop/connectors/base.py tests/connectors/test_base.py
git commit -m "feat: add abstract MusicConnector interface"
```

---

### Task 2: Developer-token signing

**Files:**
- Modify: `pyproject.toml` (add `pyjwt[crypto]`)
- Create: `src/needledrop/connectors/apple_token.py`
- Test: `tests/connectors/test_apple_token.py`

- [ ] **Step 1: Add the dependency and reinstall**

In `pyproject.toml`, add `"pyjwt[crypto]"` to the `[project] dependencies` list (after `"keyring"`):

```toml
    "keyring",
    "pyjwt[crypto]",
```

Then: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pip install --no-cache-dir -e ".[dev]"`

- [ ] **Step 2: Write the failing test**

`tests/connectors/test_apple_token.py`:

```python
import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from needledrop.connectors.apple_token import make_developer_token


def _p8_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


def test_make_developer_token_has_expected_header_and_claims():
    pem = _p8_pem()
    token = make_developer_token(pem, team_id="TEAM000000", key_id="KEY0000000", now=1_700_000_000, ttl=3600)

    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "KEY0000000"

    public_key = serialization.load_pem_private_key(pem.encode(), password=None).public_key()
    claims = jwt.decode(token, public_key, algorithms=["ES256"])
    assert claims["iss"] == "TEAM000000"
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_000 + 3600


def test_make_developer_token_is_a_str():
    token = make_developer_token(_p8_pem(), team_id="T", key_id="K")
    assert isinstance(token, str)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_token.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'needledrop.connectors.apple_token'`.

- [ ] **Step 4: Implement**

`src/needledrop/connectors/apple_token.py`:

```python
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
```

> Note: the test calls `make_developer_token(pem, team_id=..., key_id=...)` with `p8_pem` positional and the rest keyword — match this signature exactly (`p8_pem` positional-or-keyword, `team_id`/`key_id`/`now`/`ttl` keyword-only after `*`).

- [ ] **Step 5: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_token.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/needledrop/connectors/apple_token.py tests/connectors/test_apple_token.py
git commit -m "feat: add Apple developer-token ES256 signing"
```

---

### Task 3: Keystore-backed Apple credentials

**Files:**
- Modify: `src/needledrop/connectors/apple_token.py`
- Test: `tests/connectors/test_apple_credentials.py`

- [ ] **Step 1: Write the failing test**

`tests/connectors/test_apple_credentials.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_credentials.py -v`
Expected: FAIL — `ImportError: cannot import name 'AppleCredentials'`.

- [ ] **Step 3: Implement (append to `apple_token.py`)**

Add `from dataclasses import dataclass` to the imports, `from needledrop.keystore import get_backend`, then append:

```python
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
```

The imports block at the top of `apple_token.py` should now be:

```python
from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

from needledrop.keystore import get_backend
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_credentials.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_token.py tests/connectors/test_apple_credentials.py
git commit -m "feat: add keystore-backed Apple credentials"
```

---

### Task 4: Provider models

**Files:**
- Create: `src/needledrop/connectors/apple_models.py`
- Test: `tests/connectors/test_apple_models.py`

- [ ] **Step 1: Write the failing test**

`tests/connectors/test_apple_models.py`:

```python
from needledrop.connectors.apple_models import (
    CatalogAlbum,
    CatalogSong,
    LibraryAlbum,
    LibraryPlaylist,
    LibrarySong,
)


def test_library_album_from_api():
    resource = {
        "id": "l.abc",
        "type": "library-albums",
        "attributes": {
            "name": "OK Computer",
            "artistName": "Radiohead",
            "trackCount": 12,
            "releaseDate": "1997-05-21",
            "dateAdded": "2023-11-01T10:00:00Z",
        },
    }
    album = LibraryAlbum.from_api(resource)
    assert album.id == "l.abc"
    assert album.name == "OK Computer"
    assert album.artist_name == "Radiohead"
    assert album.track_count == 12
    assert album.release_date == "1997-05-21"
    assert album.date_added == "2023-11-01T10:00:00Z"


def test_library_song_maps_duration():
    resource = {
        "id": "l.s1",
        "type": "library-songs",
        "attributes": {
            "name": "Karma Police",
            "artistName": "Radiohead",
            "albumName": "OK Computer",
            "durationInMillis": 261000,
            "trackNumber": 6,
            "discNumber": 1,
        },
    }
    song = LibrarySong.from_api(resource)
    assert song.duration_ms == 261000
    assert song.track_number == 6
    assert song.disc_number == 1
    assert song.album_name == "OK Computer"


def test_library_playlist_flattens_description():
    resource = {
        "id": "p.1",
        "type": "library-playlists",
        "attributes": {"name": "Faves", "description": {"standard": "my favourites"}},
    }
    pl = LibraryPlaylist.from_api(resource)
    assert pl.name == "Faves"
    assert pl.description == "my favourites"


def test_library_playlist_missing_description():
    pl = LibraryPlaylist.from_api({"id": "p.2", "attributes": {"name": "X"}})
    assert pl.description is None


def test_catalog_album_carries_upc():
    resource = {
        "id": "1109714933",
        "type": "albums",
        "attributes": {"name": "OK Computer", "artistName": "Radiohead", "upc": "634904032463",
                       "trackCount": 12, "releaseDate": "1997-05-21"},
    }
    album = CatalogAlbum.from_api(resource)
    assert album.id == "1109714933"
    assert album.upc == "634904032463"
    assert album.track_count == 12


def test_catalog_song_carries_isrc():
    resource = {
        "id": "1109714945",
        "type": "songs",
        "attributes": {"name": "Karma Police", "artistName": "Radiohead",
                       "isrc": "GBAYE9700116", "albumName": "OK Computer"},
    }
    song = CatalogSong.from_api(resource)
    assert song.isrc == "GBAYE9700116"
    assert song.album_name == "OK Computer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_models.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/connectors/apple_models.py`:

```python
"""Provider-shaped Pydantic models parsed from Apple Music API JSON.

These mirror what Apple returns (library + catalog resources). Mapping them into
the canonical store + matching happens in a later plan; the connector only fetches
and parses.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LibraryAlbum(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    track_count: int | None = None
    release_date: str | None = None
    date_added: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> "LibraryAlbum":
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            track_count=a.get("trackCount"),
            release_date=a.get("releaseDate"),
            date_added=a.get("dateAdded"),
        )


class LibrarySong(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    album_name: str | None = None
    duration_ms: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    release_date: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> "LibrarySong":
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            album_name=a.get("albumName"),
            duration_ms=a.get("durationInMillis"),
            track_number=a.get("trackNumber"),
            disc_number=a.get("discNumber"),
            release_date=a.get("releaseDate"),
        )


class LibraryPlaylist(BaseModel):
    id: str
    name: str
    description: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> "LibraryPlaylist":
        a = resource.get("attributes", {})
        description = a.get("description", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            description=description.get("standard") if isinstance(description, dict) else None,
        )


class CatalogAlbum(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    upc: str | None = None
    track_count: int | None = None
    release_date: str | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> "CatalogAlbum":
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            upc=a.get("upc"),
            track_count=a.get("trackCount"),
            release_date=a.get("releaseDate"),
        )


class CatalogSong(BaseModel):
    id: str
    name: str
    artist_name: str | None = None
    album_name: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None

    @classmethod
    def from_api(cls, resource: dict[str, Any]) -> "CatalogSong":
        a = resource.get("attributes", {})
        return cls(
            id=resource["id"],
            name=a.get("name", ""),
            artist_name=a.get("artistName"),
            album_name=a.get("albumName"),
            isrc=a.get("isrc"),
            duration_ms=a.get("durationInMillis"),
        )


class CatalogSearchResult(BaseModel):
    albums: list[CatalogAlbum] = Field(default_factory=list)
    songs: list[CatalogSong] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_models.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_models.py tests/connectors/test_apple_models.py
git commit -m "feat: add Apple provider models"
```

---

### Task 5: Read-only Apple Music HTTP client

**Files:**
- Create: `src/needledrop/connectors/apple_music.py`
- Test: `tests/connectors/test_apple_music.py`

- [ ] **Step 1: Write the failing test**

`tests/connectors/test_apple_music.py`:

```python
import httpx

from needledrop.connectors.apple_token import AppleCredentials
from needledrop.connectors.apple_music import AppleMusicConnector

CREDS = AppleCredentials(team_id="T", key_id="K", p8_pem="PEM", user_token="UTOK")


def _connector(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url=AppleMusicConnector.BASE_URL)
    # token signing is exercised elsewhere; inject a fixed developer token to avoid real keys
    return AppleMusicConnector(CREDS, client=client, developer_token="DEVTOK")


def test_get_storefront_sends_both_tokens():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        seen["mut"] = request.headers.get("Music-User-Token")
        assert request.url.path == "/v1/me/storefront"
        return httpx.Response(200, json={"data": [{"id": "us", "type": "storefronts"}]})

    assert _connector(handler).get_storefront() == "us"
    assert seen["auth"] == "Bearer DEVTOK"
    assert seen["mut"] == "UTOK"


def test_iter_library_albums_follows_next():
    def handler(request):
        if request.url.params.get("offset") == "100":
            return httpx.Response(200, json={"data": [{"id": "l.b", "attributes": {"name": "B"}}]})
        return httpx.Response(200, json={
            "data": [{"id": "l.a", "attributes": {"name": "A"}}],
            "next": "/v1/me/library/albums?offset=100",
            "meta": {"total": 2},
        })

    albums = list(_connector(handler).iter_library_albums())
    assert [a.id for a in albums] == ["l.a", "l.b"]
    assert [a.name for a in albums] == ["A", "B"]


def test_search_catalog_parses_results():
    def handler(request):
        assert request.url.path == "/v1/catalog/us/search"
        assert request.url.params["term"] == "radiohead"
        assert request.headers.get("Music-User-Token") is None  # catalog needs only dev token
        return httpx.Response(200, json={"results": {
            "albums": {"data": [{"id": "a1", "attributes": {"name": "OK Computer", "upc": "123"}}]},
            "songs": {"data": [{"id": "s1", "attributes": {"name": "Karma Police", "isrc": "GB1"}}]},
        }})

    result = _connector(handler).search_catalog("us", "radiohead")
    assert result.albums[0].upc == "123"
    assert result.songs[0].isrc == "GB1"


def test_missing_user_token_raises_for_library():
    creds = AppleCredentials(team_id="T", key_id="K", p8_pem="PEM", user_token=None)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
                          base_url=AppleMusicConnector.BASE_URL)
    connector = AppleMusicConnector(creds, client=client, developer_token="DEVTOK")
    import pytest

    with pytest.raises(RuntimeError) as exc:
        connector.get_storefront()
    assert "login" in str(exc.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_music.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

`src/needledrop/connectors/apple_music.py`:

```python
"""Read-only Apple Music API client."""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from needledrop.connectors.apple_models import (
    CatalogAlbum,
    CatalogSearchResult,
    CatalogSong,
    LibraryAlbum,
    LibraryPlaylist,
    LibrarySong,
)
from needledrop.connectors.apple_token import AppleCredentials, load_credentials, make_developer_token
from needledrop.connectors.base import MusicConnector


class AppleMusicConnector(MusicConnector):
    """Reads the user's Apple Music library and searches the catalog.

    Mutating operations are intentionally absent (added in a later plan).
    """

    BASE_URL = "https://api.music.apple.com"
    LIBRARY_PAGE_LIMIT = 100

    def __init__(
        self,
        credentials: AppleCredentials,
        *,
        client: httpx.Client | None = None,
        developer_token: str | None = None,
    ) -> None:
        self._creds = credentials
        self._developer_token = developer_token or make_developer_token(
            credentials.p8_pem, team_id=credentials.team_id, key_id=credentials.key_id
        )
        self._client = client or httpx.Client(base_url=self.BASE_URL, timeout=30.0)

    @classmethod
    def from_keystore(cls) -> "AppleMusicConnector":
        return cls(load_credentials())

    def _headers(self, *, user: bool) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._developer_token}"}
        if user:
            if not self._creds.user_token:
                raise RuntimeError(
                    "Music User Token missing — run `needledrop auth apple login`."
                )
            headers["Music-User-Token"] = self._creds.user_token
        return headers

    def get_storefront(self) -> str:
        response = self._client.get("/v1/me/storefront", headers=self._headers(user=True))
        response.raise_for_status()
        return response.json()["data"][0]["id"]

    def _paginate(self, path: str) -> Iterator[dict]:
        next_url: str | None = f"{path}?limit={self.LIBRARY_PAGE_LIMIT}"
        while next_url:
            response = self._client.get(next_url, headers=self._headers(user=True))
            response.raise_for_status()
            body = response.json()
            yield from body.get("data", [])
            next_url = body.get("next")

    def iter_library_albums(self) -> Iterator[LibraryAlbum]:
        for resource in self._paginate("/v1/me/library/albums"):
            yield LibraryAlbum.from_api(resource)

    def iter_library_songs(self) -> Iterator[LibrarySong]:
        for resource in self._paginate("/v1/me/library/songs"):
            yield LibrarySong.from_api(resource)

    def iter_library_playlists(self) -> Iterator[LibraryPlaylist]:
        for resource in self._paginate("/v1/me/library/playlists"):
            yield LibraryPlaylist.from_api(resource)

    def search_catalog(
        self, storefront: str, term: str, types: tuple[str, ...] = ("albums", "songs"), limit: int = 25
    ) -> CatalogSearchResult:
        response = self._client.get(
            f"/v1/catalog/{storefront}/search",
            params={"term": term, "types": ",".join(types), "limit": limit},
            headers=self._headers(user=False),
        )
        response.raise_for_status()
        results = response.json().get("results", {})
        albums = [CatalogAlbum.from_api(x) for x in results.get("albums", {}).get("data", [])]
        songs = [CatalogSong.from_api(x) for x in results.get("songs", {}).get("data", [])]
        return CatalogSearchResult(albums=albums, songs=songs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_music.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_music.py tests/connectors/test_apple_music.py
git commit -m "feat: add read-only Apple Music API client"
```

---

### Task 6: MusicKit-JS auth helper

**Files:**
- Create: `src/needledrop/connectors/apple_auth.py`
- Test: `tests/connectors/test_apple_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/connectors/test_apple_auth.py`:

```python
import pytest

from needledrop.connectors.apple_auth import (
    CALLBACK_PATH,
    build_auth_page,
    extract_user_token,
)


def test_build_auth_page_embeds_token_and_musickit():
    html = build_auth_page("DEVTOKEN123", app_name="NeedleDrop")
    assert "DEVTOKEN123" in html
    assert "js-cdn.music.apple.com/musickit/v3/musickit.js" in html
    assert "MusicKit.configure" in html
    assert "authorize" in html
    assert "NeedleDrop" in html
    assert CALLBACK_PATH in html


def test_extract_user_token_from_form_body():
    assert extract_user_token("musicUserToken=abc123&other=x") == "abc123"


def test_extract_user_token_url_decodes():
    assert extract_user_token("musicUserToken=ab%2Bcd%3D") == "ab+cd="


def test_extract_user_token_missing_raises():
    with pytest.raises(ValueError):
        extract_user_token("nothing=here")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_auth.py -v`
Expected: FAIL — `ModuleNotFoundError`.

**Security note (Subresource Integrity):** the page loads MusicKit JS from Apple's
CDN without an `integrity="sha384-…"` SRI hash. This is intentional and correct
here: Apple serves `musickit/v3/musickit.js` as a mutable, non-versioned script
and publishes no stable hash, so a pinned SRI would break on every Apple update.
The residual risk (trusting Apple's first-party CDN) is acceptable because the page
is served only on `localhost` during a one-time, user-initiated authorization and
loads Apple's own origin. Do not add an `integrity` attribute to the MusicKit
script tag — it will cause the auth flow to fail when Apple rotates the file.

- [ ] **Step 3: Implement**

`src/needledrop/connectors/apple_auth.py`:

```python
"""MusicKit-JS authorization helper: serve a local page that obtains a Music User
Token in the browser and POSTs it back, then persist it to the keystore.

The browser flow itself is a manual/interactive path; the pure pieces (page
rendering, token extraction) are unit-tested, and the live server loop is driven
by `needledrop auth apple login`.
"""

from __future__ import annotations

import http.server
import threading
import webbrowser
from urllib.parse import parse_qs

from needledrop.connectors.apple_token import store_user_token

CALLBACK_PATH = "/callback"

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>NeedleDrop — Apple Music authorization</title></head>
<body>
<h1>Authorizing {app_name} with Apple Music…</h1>
<p id="status">Loading MusicKit…</p>
<script src="https://js-cdn.music.apple.com/musickit/v3/musickit.js"></script>
<script>
document.addEventListener('musickitloaded', function () {{
  MusicKit.configure({{ developerToken: '{developer_token}', app: {{ name: '{app_name}', build: '1.0' }} }});
  MusicKit.getInstance().authorize().then(function (musicUserToken) {{
    document.getElementById('status').textContent = 'Authorized. You can close this tab.';
    return fetch('{callback_path}', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
      body: 'musicUserToken=' + encodeURIComponent(musicUserToken),
    }});
  }}).catch(function (err) {{
    document.getElementById('status').textContent = 'Authorization failed: ' + err;
  }});
}});
</script>
</body>
</html>
"""


def build_auth_page(developer_token: str, *, app_name: str = "NeedleDrop") -> str:
    """Render the local MusicKit-JS authorization page."""
    return _PAGE_TEMPLATE.format(
        developer_token=developer_token, app_name=app_name, callback_path=CALLBACK_PATH
    )


def extract_user_token(form_body: str) -> str:
    """Pull the `musicUserToken` value out of a urlencoded form body."""
    values = parse_qs(form_body).get("musicUserToken")
    if not values:
        raise ValueError("musicUserToken not present in callback body")
    return values[0]


def run_auth_helper(
    developer_token: str, *, port: int, app_name: str = "NeedleDrop",
    open_browser: bool = True, timeout: float = 300.0,
) -> str:
    """Serve the auth page on localhost, capture the posted Music User Token,
    persist it, and return it. Manual/interactive path (not unit-tested)."""
    page = build_auth_page(developer_token, app_name=app_name)
    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence default logging
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode("utf-8"))

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            try:
                token = extract_user_token(body)
            except ValueError:
                self.send_response(400)
                self.end_headers()
                return
            captured["token"] = token
            store_user_token(token)
            self.send_response(204)
            self.end_headers()
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/"
        if open_browser:
            webbrowser.open(url)
        if not done.wait(timeout=timeout):
            raise TimeoutError("Timed out waiting for Apple Music authorization")
        return captured["token"]
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/connectors/test_apple_auth.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/needledrop/connectors/apple_auth.py tests/connectors/test_apple_auth.py
git commit -m "feat: add MusicKit-JS auth helper"
```

---

### Task 7: `auth apple` CLI commands

**Files:**
- Modify: `src/needledrop/cli.py`
- Test: `tests/test_cli_auth.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_auth.py`:

```python
from unittest.mock import patch

from typer.testing import CliRunner

import needledrop.keystore as keystore
from needledrop.cli import app
from needledrop.connectors.apple_token import KEY_P8, KEY_TEAM_ID

runner = CliRunner()


class InMemoryBackend:
    def __init__(self):
        self._d = {}

    def get(self, key):
        return self._d.get(key)

    def set(self, key, value):
        self._d[key] = value

    def delete(self, key):
        self._d.pop(key, None)


def test_set_credentials_stores_to_keystore(tmp_path):
    p8 = tmp_path / "AuthKey.p8"
    p8.write_text("-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----\n")
    original = keystore.get_backend()
    keystore.set_backend(InMemoryBackend())
    try:
        result = runner.invoke(
            app,
            ["auth", "apple", "set-credentials", "--team-id", "T1", "--key-id", "K1",
             "--p8", str(p8)],
        )
        assert result.exit_code == 0
        assert keystore.get_backend().get(KEY_TEAM_ID) == "T1"
        assert "BEGIN PRIVATE KEY" in keystore.get_backend().get(KEY_P8)
    finally:
        keystore.set_backend(original)


def test_login_runs_helper_and_reports():
    with patch("needledrop.cli.load_credentials") as load, \
         patch("needledrop.cli.make_developer_token", return_value="DEVTOK"), \
         patch("needledrop.cli.run_auth_helper", return_value="user-token-123") as helper:
        load.return_value = type("C", (), {"p8_pem": "P", "team_id": "T", "key_id": "K"})()
        result = runner.invoke(app, ["auth", "apple", "login"])
    assert result.exit_code == 0
    assert helper.called
    assert "Authorized" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_auth.py -v`
Expected: FAIL — `auth` command not found (exit code != 0 / import error for the patched names).

- [ ] **Step 3: Implement (modify `src/needledrop/cli.py`)**

Add these imports near the top of `cli.py` (after the existing imports):

```python
from pathlib import Path

from needledrop.config import load_settings
from needledrop.connectors.apple_auth import run_auth_helper
from needledrop.connectors.apple_token import (
    load_credentials,
    make_developer_token,
    store_developer_credentials,
)
```

(`load_settings` may already be imported from Plan 2 — if so, don't duplicate it.)

Then add the `auth` command group (after the existing `mb_app` wiring, before `def main()`):

```python
auth_app = typer.Typer(help="Authentication", no_args_is_help=True)
apple_auth_app = typer.Typer(help="Apple Music authentication", no_args_is_help=True)
auth_app.add_typer(apple_auth_app, name="apple")
app.add_typer(auth_app, name="auth")


@apple_auth_app.command("set-credentials")
def apple_set_credentials(
    team_id: str = typer.Option(..., "--team-id", help="Apple Developer Team ID"),
    key_id: str = typer.Option(..., "--key-id", help="MusicKit Key ID"),
    p8: Path = typer.Option(..., "--p8", help="Path to the MusicKit .p8 private key"),
) -> None:
    """Store Apple developer credentials (Team ID, Key ID, .p8) in the keystore."""
    store_developer_credentials(team_id=team_id, key_id=key_id, p8_pem=p8.read_text())
    typer.echo("Stored Apple developer credentials.")


@apple_auth_app.command("login")
def apple_login() -> None:
    """Authorize Apple Music in the browser and capture the Music User Token."""
    creds = load_credentials()
    developer_token = make_developer_token(
        creds.p8_pem, team_id=creds.team_id, key_id=creds.key_id
    )
    settings = load_settings()
    run_auth_helper(developer_token, port=settings.auth_port)
    typer.echo("Authorized — Music User Token stored.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest tests/test_cli_auth.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Full suite + lint gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m pytest`
Run: `/opt/homebrew/Caskroom/miniforge/base/envs/needledrop/bin/python -m ruff check .`
Expected: all pass; ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/needledrop/cli.py tests/test_cli_auth.py
git commit -m "feat: add auth apple set-credentials and login commands"
```

---

## Self-Review

**1. Spec coverage (spec §6.1, §6.2, decision 4, build step 3):**
- Developer JWT signed from the `.p8` → `apple_token.make_developer_token` (Task 2). ✓
- Read library albums/songs/playlists (paginated) → `AppleMusicConnector.iter_library_*` + `_paginate` follows `next` (Task 5). ✓
- Search catalog → `search_catalog` with storefront (Task 5); `get_storefront` (Task 5). ✓
- Provider parsing into Pydantic models → `apple_models` (Task 4). ✓
- `apple_auth.py` MusicKit-JS localhost helper capturing the user token → `build_auth_page`/`run_auth_helper`/`store_user_token` (Tasks 3, 6). ✓
- Abstract connector interface → `base.py` (Task 1). ✓
- `needledrop auth apple` CLI → `set-credentials` + `login` (Task 7). ✓
- Deferred by design: mutations (`add_album` etc.) — Plan 6; mapping provider models → canonical + matching — Plan 4.

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code; every run step has the command and expected result. The `run_auth_helper` live server loop is explicitly the manual path (its pure helpers `build_auth_page`/`extract_user_token` are unit-tested).

**3. Type/name consistency:** `make_developer_token(p8_pem, *, team_id, key_id, now=None, ttl=...)` signature is identical across its definition (Task 2) and all callers (`AppleMusicConnector.__init__` Task 5, `apple_login` Task 7, tests). `AppleCredentials(team_id, key_id, p8_pem, user_token=None)` fields match across Tasks 3/5/7. `store_user_token`/`store_developer_credentials`/`load_credentials` names match between `apple_token` (Task 3), `apple_auth` (Task 6), and `cli` (Task 7). `AppleMusicConnector(credentials, *, client=None, developer_token=None)` matches its tests (Task 5). `CALLBACK_PATH`/`build_auth_page`/`extract_user_token` match between `apple_auth` and its tests. The connector implements every abstract method declared in `base.MusicConnector` (Task 1).
