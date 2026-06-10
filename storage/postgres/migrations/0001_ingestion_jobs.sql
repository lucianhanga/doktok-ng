-- M1: ingestion jobs (brief section 16). Documents and other tables arrive in later milestones.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id            text PRIMARY KEY,
    document_id   text,
    source_path   text NOT NULL,
    status        text NOT NULL,
    detected_mime text,
    sha256        text,
    error_code    text,
    error_message text,
    started_at    timestamptz,
    finished_at   timestamptz,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_sha256 ON ingestion_jobs (sha256);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created_at ON ingestion_jobs (created_at DESC);
