from unittest.mock import patch

from typer.testing import CliRunner

from needledrop.cli import app

runner = CliRunner()


def test_mb_import_invokes_importer():
    with patch("needledrop.cli.import_musicbrainz") as mock_import:
        mock_import.return_value = {"schema_sequence": 31, "tag": "v-x", "tables": ["artist"]}
        result = runner.invoke(app, ["mb", "import"])
    assert result.exit_code == 0
    assert mock_import.called
    assert "31" in result.stdout


def test_help_lists_mb():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "mb" in result.stdout
