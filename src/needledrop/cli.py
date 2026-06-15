"""NeedleDrop operator CLI."""

from __future__ import annotations

from pathlib import Path

import typer

from needledrop.config import load_settings
from needledrop.connectors.apple_auth import run_auth_helper
from needledrop.connectors.apple_token import (
    load_credentials,
    make_developer_token,
    store_developer_credentials,
)
from needledrop.musicbrainz.importer import import_musicbrainz

app = typer.Typer(help="NeedleDrop — intelligent music library management", no_args_is_help=True)
mb_app = typer.Typer(help="MusicBrainz authority data", no_args_is_help=True)
app.add_typer(mb_app, name="mb")


@mb_app.command("import")
def mb_import() -> None:
    """Download the MusicBrainz export and materialize it into the local DuckDB."""
    summary = import_musicbrainz(load_settings())
    typer.echo(
        f"Imported MusicBrainz schema sequence {summary['schema_sequence']} "
        f"(tag {summary['tag']}): {len(summary['tables'])} tables materialized."
    )


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


def main() -> None:
    app()
