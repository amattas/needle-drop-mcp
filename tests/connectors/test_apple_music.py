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
