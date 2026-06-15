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
