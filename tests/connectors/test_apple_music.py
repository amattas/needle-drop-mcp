import httpx

from needledrop.connectors.apple_music import AppleMusicConnector
from needledrop.connectors.apple_token import AppleCredentials

CREDS = AppleCredentials(team_id="T", key_id="K", p8_pem="PEM", user_token="UTOK")


def _connector(handler):
    client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url=AppleMusicConnector.BASE_URL
    )
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
            return httpx.Response(
                200, json={"data": [{"id": "l.b", "attributes": {"name": "B"}}]}
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "l.a", "attributes": {"name": "A"}}],
                "next": "/v1/me/library/albums?offset=100",
                "meta": {"total": 2},
            },
        )

    albums = list(_connector(handler).iter_library_albums())
    assert [a.id for a in albums] == ["l.a", "l.b"]
    assert [a.name for a in albums] == ["A", "B"]


def test_paginate_retries_transient_5xx(monkeypatch):
    import needledrop.connectors.apple_music as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"errors": [{"status": "500"}]})
        return httpx.Response(200, json={"data": [{"id": "l.a", "attributes": {"name": "A"}}]})

    albums = list(_connector(handler).iter_library_albums())
    assert [a.id for a in albums] == ["l.a"]
    assert calls["n"] == 2  # one failure retried, then success


def test_paginate_raises_after_exhausting_retries(monkeypatch):
    import pytest

    import needledrop.connectors.apple_music as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        list(_connector(handler).iter_library_albums())
    assert calls["n"] == AppleMusicConnector.MAX_PAGE_RETRIES


def test_paginate_does_not_retry_client_error(monkeypatch):
    import pytest

    import needledrop.connectors.apple_music as mod

    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        list(_connector(handler).iter_library_albums())
    assert calls["n"] == 1  # 4xx is not retried


def test_search_catalog_parses_results():
    def handler(request):
        assert request.url.path == "/v1/catalog/us/search"
        assert request.url.params["term"] == "radiohead"
        assert request.headers.get("Music-User-Token") is None
        return httpx.Response(
            200,
            json={
                "results": {
                    "albums": {
                        "data": [
                            {"id": "a1", "attributes": {"name": "OK Computer", "upc": "123"}}
                        ]
                    },
                    "songs": {
                        "data": [
                            {"id": "s1", "attributes": {"name": "Karma Police", "isrc": "GB1"}}
                        ]
                    },
                }
            },
        )

    result = _connector(handler).search_catalog("us", "radiohead")
    assert result.albums[0].upc == "123"
    assert result.songs[0].isrc == "GB1"


def test_missing_user_token_raises_for_library():
    import pytest

    creds = AppleCredentials(team_id="T", key_id="K", p8_pem="PEM", user_token=None)
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []})),
        base_url=AppleMusicConnector.BASE_URL,
    )
    connector = AppleMusicConnector(creds, client=client, developer_token="DEVTOK")

    with pytest.raises(RuntimeError) as exc:
        connector.get_storefront()
    assert "login" in str(exc.value)


def test_iter_library_albums_requests_include_catalog():
    seen = {}

    def handler(request):
        seen["include"] = request.url.params.get("include")
        return httpx.Response(200, json={"data": [
            {"id": "l.a", "attributes": {"name": "A"},
             "relationships": {"catalog": {"data": [{"id": "1", "type": "albums",
                                                     "attributes": {"upc": "U1"}}]}}}
        ]})

    albums = list(_connector(handler).iter_library_albums())
    assert seen["include"] == "catalog"
    assert albums[0].upc == "U1"


def test_iter_library_playlists_does_not_request_include():
    seen = {}

    def handler(request):
        seen["include"] = request.url.params.get("include")
        return httpx.Response(200, json={"data": [{"id": "p.1", "attributes": {"name": "Faves"}}]})

    list(_connector(handler).iter_library_playlists())
    assert seen["include"] is None


def test_add_albums_to_library_posts_catalog_ids():
    from urllib.parse import unquote

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = unquote(str(request.url))
        seen["muth"] = request.headers.get("Music-User-Token")
        return httpx.Response(202)

    _connector(handler).add_albums_to_library(["1440857781", "1440857782"])
    assert seen["method"] == "POST"
    assert "/v1/me/library" in seen["url"]
    assert "1440857781,1440857782" in seen["url"]
    assert seen["muth"] == "UTOK"


def test_remove_album_from_library_deletes_by_library_id():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        return httpx.Response(204)

    _connector(handler).remove_album_from_library("l.123")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/v1/me/library/albums/l.123")


def test_create_playlist_posts_attributes_and_tracks():
    import json

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "data": [
                    {
                        "id": "p.1",
                        "attributes": {
                            "name": "Cleanup",
                            "description": {"standard": "auto"},
                        },
                    }
                ]
            },
        )

    playlist = _connector(handler).create_playlist(
        "Cleanup", description="auto", track_ids=["s.1", "s.2"]
    )
    assert seen["method"] == "POST"
    assert seen["url"].endswith("/v1/me/library/playlists")
    assert seen["body"]["attributes"]["name"] == "Cleanup"
    assert seen["body"]["attributes"]["description"] == "auto"
    assert [t["id"] for t in seen["body"]["relationships"]["tracks"]["data"]] == ["s.1", "s.2"]
    assert playlist.id == "p.1"
    assert playlist.name == "Cleanup"
