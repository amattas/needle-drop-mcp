# MB import — manual verification (full run)

The full import is not a CI gate (≈7 GB download + multi-GB Postgres load). Verify
it manually after changing the import pipeline:

1. Ensure Docker is running and `postgres:18` can be pulled.
2. Run: `needledrop mb import`  (or `python -m needledrop.cli mb import`)
3. Expect it to: read LATEST, resolve the SCHEMA_SEQUENCE -> tag, download mbdump.tar.bz2,
   start the ephemeral container, apply the 4 DDL files, COPY all core tables, materialize
   `mb_*` into the DuckDB at `NEEDLEDROP_DB_PATH`, then remove the container.
4. Spot-check in DuckDB:
   - `SELECT count(*) FROM mb_artist;`  (expect ~2.5M+)
   - `SELECT count(*) FROM mb_release_group;`
   - `SELECT count(*) FROM mb_isrc;`
   - Confirm no container remains: `docker ps -a | grep needledrop-mb-import` -> empty.
5. If the SCHEMA_SEQUENCE is unknown, the run fails loudly before the big download —
   add the new mapping to `needledrop.musicbrainz.schema_sql.SCHEMA_SEQUENCE_TAGS`.
