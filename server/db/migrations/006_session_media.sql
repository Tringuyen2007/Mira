-- Raw video is stored in private object storage, never in Timescale rows.  Timescale indexes its lifecycle,
-- timeline and permissions so a therapist/patient can ask for a precise moment without scanning media files.
CREATE TABLE IF NOT EXISTS session_media_assets (
  id                bigserial PRIMARY KEY,
  session_id        uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  kind              text NOT NULL CHECK (kind IN ('camera_hls_master', 'camera_segment', 'mesh_asset')),
  storage_key       text NOT NULL UNIQUE,
  content_type      text NOT NULL,
  byte_size         bigint NOT NULL CHECK (byte_size >= 0),
  sha256            text NOT NULL,
  start_ms          bigint,
  duration_ms       integer,
  state             text NOT NULL DEFAULT 'staged' CHECK (state IN ('staged', 'ready', 'deleted')),
  patient_consent   boolean NOT NULL DEFAULT false,
  patient_visible   boolean NOT NULL DEFAULT false,
  retain_until      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CHECK ((kind = 'camera_segment') = (start_ms IS NOT NULL AND duration_ms IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS session_media_assets_timeline_idx
  ON session_media_assets (session_id, start_ms) WHERE state = 'ready';
CREATE INDEX IF NOT EXISTS session_media_assets_expiry_idx
  ON session_media_assets (retain_until) WHERE state <> 'deleted';

INSERT INTO schema_migrations (name) VALUES ('006_session_media.sql') ON CONFLICT (name) DO NOTHING;
