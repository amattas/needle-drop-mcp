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


def test_serve_wires_catalog_search():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        create_server_mock.return_value = MagicMock()
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert "catalog_search" in create_server_mock.call_args.kwargs


def test_serve_wires_mutator():
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db"), \
         patch("needledrop.cli.create_server") as create_server_mock:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        create_server_mock.return_value = MagicMock()
        result = runner.invoke(app, ["serve"])
    assert result.exit_code == 0
    assert "mutator" in create_server_mock.call_args.kwargs


def test_serve_sync_runner_closure_invokes_sync_library():
    # Exercise the REAL sync_runner closure serve() builds (not an injected stub):
    # it must build the connector from the keystore and run sync_library against
    # the same connection open_db returned.
    with patch("needledrop.cli.load_settings") as load_settings_mock, \
         patch("needledrop.cli.open_db") as open_db_mock, \
         patch("needledrop.cli.create_server") as create_server_mock, \
         patch("needledrop.cli.AppleMusicConnector") as connector_cls, \
         patch("needledrop.cli.sync_library") as sync_fn:
        load_settings_mock.return_value = MagicMock(db_path=":memory:")
        create_server_mock.return_value = MagicMock()
        sync_fn.return_value = {"added": 1, "removed": 0, "present": 9}
        result = runner.invoke(app, ["serve"])
        sync_runner = create_server_mock.call_args.kwargs["sync_runner"]
        summary = sync_runner()
    assert result.exit_code == 0
    assert connector_cls.from_keystore.called
    # The closure must run sync_library against the connection open_db returned.
    assert sync_fn.call_args.args[1] is open_db_mock.return_value
    assert summary == {"added": 1, "removed": 0, "present": 9}
