from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_serve_builds_and_runs_server():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db") as open_db_mock, \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        server = MagicMock()
        create_server_mock.return_value = server
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert open_db_mock.called
    assert create_server_mock.called
    # The server must be run over stdio with the banner suppressed.
    server.run.assert_called_once_with(show_banner=False)
    # A sync_runner must be wired in so trigger_sync works at runtime.
    assert "sync_runner" in create_server_mock.call_args.kwargs
