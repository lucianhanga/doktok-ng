# syntax=docker/dockerfile:1
# Postgres 17 + pgvector + pgBackRest for continuous WAL archiving / PITR on the box (M12 DB-A1).
# pgBackRest must live in the db image because Postgres' archive_command runs inside this container.
# Base pinned by tag here; pin by digest at publish time. Review-grade: built/run on the box.
#
# Pin the pgBackRest version (#346): set PGBACKREST_VERSION to a Debian package version (apt-cache
# madison pgbackrest) for reproducible builds. NOTE: pgBackRest was archived 2026-04-27 then revived
# 2026-05-18 under a sponsor coalition (a pgxbackup fork exists); re-check its maintenance health
# before bumping. Leaving it empty installs the repo's current version.
FROM pgvector/pgvector:pg17
ARG PGBACKREST_VERSION=""
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        "pgbackrest${PGBACKREST_VERSION:+=$PGBACKREST_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
