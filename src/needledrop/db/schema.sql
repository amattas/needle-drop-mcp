-- NeedleDrop canonical schema (baseline, idempotent).
-- mb_* authority tables are created by `needledrop mb import`, not here.

CREATE SEQUENCE IF NOT EXISTS seq_artists START 1;
CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_artists'),
    mbid VARCHAR,
    canonical_name VARCHAR NOT NULL,
    sort_name VARCHAR,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_albums START 1;
CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_albums'),
    release_group_mbid VARCHAR,
    release_mbid VARCHAR,
    artist_id INTEGER REFERENCES artists(id),
    title VARCHAR NOT NULL,
    version_class VARCHAR,
    total_tracks INTEGER,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_tracks START 1;
CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_tracks'),
    recording_mbid VARCHAR,
    album_id INTEGER REFERENCES albums(id),
    artist_id INTEGER REFERENCES artists(id),
    title VARCHAR NOT NULL,
    isrc VARCHAR,
    disc_number INTEGER,
    track_number INTEGER,
    duration_ms INTEGER,
    external_ids_json VARCHAR NOT NULL DEFAULT '{}'
);

-- canonical_id is a polymorphic soft reference (album or track by item_type),
-- so it is intentionally not a foreign key.
CREATE SEQUENCE IF NOT EXISTS seq_library_items START 1;
CREATE TABLE IF NOT EXISTS library_items (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_library_items'),
    service VARCHAR NOT NULL,
    service_item_id VARCHAR NOT NULL,
    item_type VARCHAR NOT NULL,
    canonical_id INTEGER,
    match_confidence DOUBLE,
    match_method VARCHAR NOT NULL DEFAULT 'none',
    added_at TIMESTAMP,
    last_seen_at TIMESTAMP,
    status VARCHAR NOT NULL DEFAULT 'present',
    UNIQUE (service, service_item_id, item_type)
);

CREATE SEQUENCE IF NOT EXISTS seq_match_candidates START 1;
CREATE TABLE IF NOT EXISTS match_candidates (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_match_candidates'),
    library_item_id INTEGER NOT NULL REFERENCES library_items(id),
    candidate_mbid VARCHAR NOT NULL,
    candidate_kind VARCHAR NOT NULL,
    score DOUBLE NOT NULL,
    method VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending'
);

CREATE SEQUENCE IF NOT EXISTS seq_playlists START 1;
CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_playlists'),
    service VARCHAR NOT NULL,
    service_playlist_id VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    description VARCHAR
);

CREATE SEQUENCE IF NOT EXISTS seq_sync_runs START 1;
CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_sync_runs'),
    service VARCHAR NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status VARCHAR NOT NULL,
    summary_json VARCHAR NOT NULL DEFAULT '{}'
);

CREATE SEQUENCE IF NOT EXISTS seq_cleanup_findings START 1;
CREATE TABLE IF NOT EXISTS cleanup_findings (
    id INTEGER PRIMARY KEY DEFAULT nextval('seq_cleanup_findings'),
    finding_type VARCHAR NOT NULL,
    severity VARCHAR NOT NULL,
    entity_id INTEGER,
    description VARCHAR NOT NULL,
    recommendation_json VARCHAR NOT NULL DEFAULT '{}',
    resolved_at TIMESTAMP,
    ignored_at TIMESTAMP
);
