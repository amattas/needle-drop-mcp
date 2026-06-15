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
    # Regression guard for a deliberate security decision: Apple's MusicKit CDN
    # script is mutable/unversioned, so no Subresource Integrity hash is pinned.
    assert "integrity" not in html


def test_extract_user_token_from_form_body():
    assert extract_user_token("musicUserToken=abc123&other=x") == "abc123"


def test_extract_user_token_url_decodes():
    assert extract_user_token("musicUserToken=ab%2Bcd%3D") == "ab+cd="


def test_extract_user_token_missing_raises():
    with pytest.raises(ValueError):
        extract_user_token("nothing=here")
