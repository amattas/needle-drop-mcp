"""Non-secret application configuration (DB path, ports, matching thresholds)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are NOT stored here — see needledrop.keystore."""

    model_config = SettingsConfigDict(
        env_prefix="NEEDLEDROP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_path: Path = Field(default=Path("./library.duckdb"))
    auth_port: int = Field(default=8787, ge=1, le=65535)
    fuzzy_threshold: float = Field(default=0.87, ge=0.0, le=1.0)

    # --- MusicBrainz import (used only by `needledrop mb import`) ---
    mb_dump_base_url: str = Field(
        default="https://data.metabrainz.org/pub/musicbrainz/data/fullexport/"
    )
    mb_server_raw_base: str = Field(
        default="https://raw.githubusercontent.com/metabrainz/musicbrainz-server"
    )
    mb_data_dir: Path = Field(default=Path("./mb-dumps"))
    mb_postgres_image: str = Field(default="postgres:18")
    mb_postgres_container: str = Field(default="needledrop-mb-import")
    mb_postgres_port: int = Field(default=55432, ge=1, le=65535)
    mb_postgres_db: str = Field(default="musicbrainz")
    mb_postgres_user: str = Field(default="musicbrainz")
    mb_postgres_password: str = Field(default="needledrop-ephemeral")


def load_settings() -> Settings:
    """Load settings from environment and optional .env file."""
    return Settings()
