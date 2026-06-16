-- Add albums.total_tracks for partial-album detection.
-- Idempotent: a no-op on fresh DBs that already created it from the baseline schema.
ALTER TABLE albums ADD COLUMN IF NOT EXISTS total_tracks INTEGER;
