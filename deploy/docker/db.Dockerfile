# syntax=docker/dockerfile:1
# Postgres 17 + pgvector + pgBackRest for continuous WAL archiving / PITR on the box (M12 DB-A1).
# pgBackRest must live in the db image because Postgres' archive_command runs inside this container.
# Base pinned by tag here; pin by digest at publish time. Review-grade: built/run on the box.
FROM pgvector/pgvector:pg17
RUN apt-get update \
    && apt-get install -y --no-install-recommends pgbackrest \
    && rm -rf /var/lib/apt/lists/*
