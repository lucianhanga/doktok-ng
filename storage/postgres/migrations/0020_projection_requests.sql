-- M7.1: the recompute queue for embedding projections (ADR-0016). There is no message broker, so a
-- recompute is a row here: the API enqueues one, the worker claims it (status -> running), fits the
-- 2D and 3D projections, then deletes it. At most one live request per tenant (UNIQUE) keeps repeated
-- button presses from piling up work.

CREATE TABLE IF NOT EXISTS projection_requests (
    id           text PRIMARY KEY,
    tenant_id    text NOT NULL UNIQUE,
    status       text NOT NULL DEFAULT 'pending',
    requested_at timestamptz NOT NULL DEFAULT now(),
    claimed_at   timestamptz
);

CREATE INDEX IF NOT EXISTS idx_projection_requests_pending
    ON projection_requests (requested_at) WHERE status = 'pending';
