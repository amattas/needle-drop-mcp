"""NeedleDrop operator CLI."""

from __future__ import annotations

import typer

from needledrop.config import load_settings
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


def main() -> None:
    app()
