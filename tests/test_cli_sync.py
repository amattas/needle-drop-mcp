from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_sync_command_reports_summary():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.AppleMusicConnector") as connector_cls, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.sync_library") as sync_fn:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        sync_fn.return_value = {"added": 3, "removed": 1, "present": 42}
        result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert connector_cls.from_keystore.called
    assert sync_fn.called
    assert "3 added" in result.stdout
    assert "1 removed" in result.stdout
    assert "42 present" in result.stdout
