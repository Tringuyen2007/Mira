-- Pose-only training-data provenance.  The MVP never stores source or patient video.
CREATE TABLE IF NOT EXISTS training_datasets (
  id                text PRIMARY KEY,
  name              text NOT NULL,
  source_url        text NOT NULL,
  license           text NOT NULL,
  approval          text NOT NULL CHECK (approval IN ('approved', 'license_review_required')),
  raw_media_allowed boolean NOT NULL DEFAULT false CHECK (raw_media_allowed = false),
  pose_format       text,
  exercises         jsonb NOT NULL,
  notes             text,
  registered_at     timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS training_rep_examples (
  id                 bigserial PRIMARY KEY,
  dataset_id         text NOT NULL REFERENCES training_datasets(id),
  external_id        text NOT NULL,
  exercise_id        text NOT NULL,
  split              text NOT NULL CHECK (split IN ('train', 'validation', 'test')),
  rep_index          int,
  started_frame      int,
  ended_frame        int,
  correctness        boolean,
  pose_uri           text NOT NULL,
  annotation         jsonb NOT NULL DEFAULT '{}',
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_id, external_id)
);
CREATE INDEX IF NOT EXISTS training_rep_examples_exercise_idx ON training_rep_examples (exercise_id, split);

INSERT INTO schema_migrations (name) VALUES ('005_training_data.sql') ON CONFLICT (name) DO NOTHING;
