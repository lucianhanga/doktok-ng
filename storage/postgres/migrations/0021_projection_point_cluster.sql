-- M7.2: HDBSCAN cluster id per projected point (ADR-0016). NULL on rows projected before clustering
-- existed; -1 means the point is noise (no cluster). The same id is used for a chunk in both the 2D
-- and 3D projections, so the "color by cluster" view agrees across dimensions.

ALTER TABLE embedding_projection_points ADD COLUMN IF NOT EXISTS cluster smallint;
