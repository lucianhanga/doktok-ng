# Backup runner (M12 #377): a tiny image with restic + the deploy scripts so the SAME
# deploy/backup-files.sh that runs on the host in dev runs here in compose/prod, with the files
# volume + the host backup dir mounted. pg backups run inside the db container (pgbackrest is there).
# Build context = repo root.
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends restic ca-certificates bash coreutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY deploy/ /app/deploy/

# Invoked as: docker compose run --rm backup-runner deploy/backup-files.sh
ENTRYPOINT []
CMD ["bash"]
