# Deploying DokTok NG to a fresh box (SSH key + sudo) — validated runbook

A step-by-step, **field-tested** procedure for standing up the hybrid DokTok NG stack on a small
Linux box (N95 / N100-class, 4 cores, 8 GB) starting from nothing but **SSH access and sudo**. This
complements [deployment-trigkey-n95.md](deployment-trigkey-n95.md) (the rationale + the
config reference) and [ADR-0020](../adr/ADR-0020-hybrid-deployment-topology.md) (why hybrid). This
document is the *how*, in the exact order that works, and it calls out the non-obvious gotchas that
will otherwise cost you an afternoon.

> **Deployment shape.** Images are **built on the box** from the source tree (no GHCR pull is assumed
> — the published-image path via `release.yml` + `deploy.yml` is optional and documented at the end).
> The app runs as `docker compose -f docker-compose.prod.yml`. Only Caddy publishes a host port.
>
> **Hybrid split.** OCR (PaddleOCR) + embeddings (Ollama `qwen3-embedding:0.6b`) run **locally**; the
> enrichment pipeline and RAG chat run on **OpenAI** (the local LLM does not fit in 8 GB). You can
> deploy **local-only** (no key) and add the OpenAI key later via the Settings UI.

## 0. What you need before you start

- SSH access to the box as a sudo-capable user, e.g.
  `ssh -i <key.pem> <user>@<box-ip>`.
- The box on Linux (Ubuntu 24.04 validated). amd64.
- Outbound internet from the box (to pull base images + PaddleOCR/embedding models; and, if you use
  the hybrid, to reach `api.openai.com`).
- The DokTok NG source tree on your workstation (this repo).
- ~10 GB free disk for images + models (the box used ~10 GB; it had 200 GB free).

## 1. Prepare the host

Bring the OS current and make sure Docker (Engine + Compose v2) is present:

```bash
sudo apt-get update && sudo apt-get -y upgrade
docker version            # need Engine + the compose plugin (docker compose version)
sudo usermod -aG docker "$USER"   # run docker without sudo (re-login or reboot to take effect)
```

If the kernel was upgraded, **reboot** (`sudo systemctl reboot`).

### Gotcha — slow boot from `systemd-networkd-wait-online`

If the box boots on **Wi-Fi** (NetworkManager) with an **unplugged wired NIC**, boot can stall ~120 s
on `systemd-networkd-wait-online` waiting for the dead wired port. If that applies, mask it (the
NetworkManager waiter still satisfies `network-online.target`):

```bash
sudo systemctl mask systemd-networkd-wait-online.service
```

## 2. Get the source onto the box

Put the tree at `/opt/doktok` (the path the deploy workflow also assumes):

```bash
ssh -i <key> <user>@<box> "sudo mkdir -p /opt/doktok && sudo chown $USER:$USER /opt/doktok"

rsync -az --delete -e "ssh -i <key>" \
  --exclude='.git' --exclude='node_modules' --exclude='**/node_modules' \
  --exclude='.venv' --exclude='**/.venv' --exclude='storage/files' --exclude='backups' \
  --exclude='dist' --exclude='apps/ui/dist' --exclude='**/__pycache__' \
  --exclude='**/.pytest_cache' --exclude='**/.mypy_cache' --exclude='**/.ruff_cache' \
  --exclude='.env' --exclude='.env.*' --exclude='*.log' --exclude='**/.DS_Store' \
  ./ <user>@<box>:/opt/doktok/
```

### Gotcha — exclude `storage/files`, NOT `storage/`

`storage/` is **both** a set of workspace source packages (`storage/filesystem`, `storage/postgres`)
**and** the local data dir `storage/files` (which can be huge). Exclude only **`storage/files`**.
Excluding all of `storage/` removes workspace packages and the image build fails with
`Distribution not found at: file:///app/storage/filesystem`.

## 3. Create `.env.production` (secrets) on the box

Generate strong secrets **on the box** so they never transit your shell history. Local-only profile
(no OpenAI key yet); for the full hybrid, fill `DOKTOK_OPENAI_API_KEY` + the provider lines.

```bash
ssh -i <key> <user>@<box> 'bash -s' <<'EOF'
cd /opt/doktok; umask 077; gen(){ openssl rand -hex "$1"; }
T="$(gen 32)"
cat > .env.production <<ENV
DOKTOK_IMAGE_TAG=latest
DOKTOK_DB_PASSWORD=$(gen 24)
DOKTOK_TENANT_TOKENS={"${T}":"default"}
DOKTOK_API_TOKEN=${T}
DOKTOK_SECRETS_KEY=$(gen 32)
DOKTOK_OPENAI_API_KEY=
DOKTOK_NO_EGRESS=false
DOKTOK_PIPELINE_PROVIDER=
DOKTOK_PIPELINE_MODEL=
DOKTOK_RAG_PROVIDER=
DOKTOK_RAG_MODEL=
DOKTOK_SITE_ADDRESS=:80
DOKTOK_CORS_ORIGINS=[]
# --- N95: 4 cores ---
DOKTOK_OCR_CONCURRENCY=2
DOKTOK_OCR_CPU_THREADS=1
DOKTOK_OCR_ENABLE_MKLDNN=false
DOKTOK_INGEST_CONCURRENCY=2
DOKTOK_OPENAI_RECONCILE_CONCURRENCY=6
DOKTOK_RATE_LIMIT_PER_MINUTE=120
DOKTOK_MAX_REQUEST_MB=25
DOKTOK_LOG_FORMAT=json
DOKTOK_LOG_LEVEL=INFO
DOKTOK_BACKUP_DIR=/var/lib/doktok/backups
DOKTOK_RESTIC_PASSWORD=$(gen 32)
DOKTOK_PGBACKREST_CIPHER_PASS=$(gen 32)
DOKTOK_AZURE_ACCOUNT=
DOKTOK_AZURE_CONTAINER=doktok-backups
DOKTOK_AZURE_SAS=
ENV
chmod 600 .env.production && echo ".env.production written"
EOF
```

Notes:
- The bearer token is **injected at the Caddy edge** — users just open `http://<box>/`; they don't
  enter the token. Keep `.env.production` (mode 600) and store the secrets off-box too.
- To enable the **hybrid** now: set `DOKTOK_OPENAI_API_KEY=sk-...`, and
  `DOKTOK_PIPELINE_PROVIDER=openai` / `DOKTOK_RAG_PROVIDER=openai` with models (e.g. `gpt-4o-mini`).
- See [the N95 config reference](deployment-trigkey-n95.md#configuration-doktok_-settings) for every
  knob.

### Why `DOKTOK_OCR_ENABLE_MKLDNN=false` on the N95

PaddlePaddle's oneDNN kernels abort under the PIR executor on Intel **N95 / Alder Lake-N**
(`NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute ... onednn_instruction.cc`).
Disabling mkldnn fixes it (validated: OCR then reads pages correctly). Leave it `true` on CPUs where
oneDNN works (it is faster there). Background and the OCR memory/OOM tuning that pairs with this flag:
[ADR-0010](../adr/ADR-0010-paddleocr-default-ocr-engine.md) and
[the N95 OCR tuning section](deployment-trigkey-n95.md#n95-throughput-tuning).

## 4. Build the images on the box

```bash
ssh -i <key> <user>@<box> \
  'cd /opt/doktok && docker compose -f docker-compose.prod.yml --env-file .env.production config -q \
   && nohup docker compose -f docker-compose.prod.yml --env-file .env.production build \
        > build.log 2>&1 & echo "building (tail -f build.log)"'
```

The **worker** image bakes in PaddleOCR + PaddlePaddle (~1.8 GB) and is the long pole. Watch
`build.log`; expect a few minutes on first build. Result: `doktok-ng-{db,backend,worker,ui}`.

## 5. First start + one-time initialization

```bash
ssh -i <key> <user>@<box> 'cd /opt/doktok && \
  docker compose -f docker-compose.prod.yml --env-file .env.production up -d'
```

Bring it up **in a way that survives a dropped SSH session** for the first run (the ollama base image
is a large pull): run it under `nohup ... &` or `tmux`, or simply re-run `up -d` if the session drops
(it is idempotent — but first `docker compose down` any half-created containers if you hit a
"container name already in use" conflict).

Then three one-time steps:

```bash
cd /opt/doktok
DC="docker compose -f docker-compose.prod.yml --env-file .env.production"

# (a) Pull the embedding model (only the embedder; never the chat models on this box):
$DC exec ollama ollama pull qwen3-embedding:0.6b

# (b) Make the files volume writable by the unprivileged app user (uid 10001):
$DC exec -u root worker chown -R 10001:10001 /data/files
$DC restart worker backend       # so the worker creates the per-tenant ingest tree

# (c) Initialize the pgBackRest stanza (WAL archiving / PITR; run as the postgres user):
$DC exec -u postgres db pgbackrest --stanza=doktok stanza-create
$DC exec -u postgres db pgbackrest --stanza=doktok check
```

### Gotcha — the files volume is root-owned on first run

A fresh named volume (`doktok-files`) is owned by `root`, but backend/worker run as uid **10001**, so
they cannot create the tenant ingest tree until you run step (b). Symptom: `/data/files` is empty and
the worker can't write. (Until this is fixed at the image level, step (b) is required on every fresh
volume.)

## 6. Verify the deployment

```bash
DC="docker compose -f docker-compose.prod.yml --env-file .env.production"
$DC ps                                   # all services Up; db/ollama/backend healthy
curl -fsS http://<box>/health            # {"status":"ok",...}
curl -fsS http://<box>/ready             # database/ollama/gotenberg/worker all "ok"
curl -fsS -o /dev/null -w '%{http_code}\n' http://<box>/   # 200 (SPA)
```

End-to-end local pipeline (OCR + embeddings), no OpenAI needed — drop a text image into the ingest
folder and confirm it becomes searchable:

```bash
$DC exec -T worker /app/.venv/bin/python -c "
from PIL import Image, ImageDraw, ImageFont
img=Image.new('RGB',(1100,320),'white'); d=ImageDraw.Draw(img)
try: f=ImageFont.load_default(size=52)
except TypeError: f=ImageFont.load_default()
d.text((40,80),'Hello DokTok 12345',fill='black',font=f)
img.save('/data/files/default/ingest/smoke.png'); print('dropped')
"
# wait ~30-60s, then:
curl -fsS 'http://<box>/api/v1/documents?limit=5'          # status should reach "active"
curl -fsS 'http://<box>/api/v1/search?q=Hello+DokTok'      # returns the document
```

The first OCR call loads/downloads the PaddleOCR models (a one-time delay).

## 7. Enable the hybrid (OpenAI) — optional, later

Open `http://<box>/` -> **Settings**: set the **Data pipeline** and **Document interrogation**
purposes to an OpenAI provider+model and enter the **OpenAI API key** (write-only). Restart
backend + worker. (Or seed via env on a fresh DB: `DOKTOK_OPENAI_API_KEY` +
`DOKTOK_PIPELINE_PROVIDER`/`DOKTOK_RAG_PROVIDER` then recreate.) OCR + embeddings stay local; only
enrichment + chat egress to OpenAI. Restrict host egress with
[`deploy/firewall-openai-only.example.nft`](../../deploy/firewall-openai-only.example.nft).

## 7b. Offloading Ollama to another host (optional)

Each Ollama-using purpose (Data pipeline, Document interrogation, Embedding) can target a **different
Ollama server** via **Settings → AI** — set a per-usage "Ollama server URL" (blank = inherit
`DOKTOK_OLLAMA_BASE_URL`; "Reset to default" clears it). This is the ADR-0020 "beefier LAN Ollama
host" option, e.g. run embeddings on a GPU box while the rest stays on the N95. Applies on the next
backend/worker restart. (M13 #369.)

## 8. Day-2 operations

```bash
DC="docker compose -f docker-compose.prod.yml --env-file .env.production"
$DC logs -f backend worker          # follow logs (JSON; bounded to 10m x5 files)
$DC restart worker                  # restart a service
$DC down                            # stop (KEEPS volumes). NEVER add -v unless you mean to wipe data.
$DC pull && $DC up -d               # update to new images (built or pulled)
```

- **Backups (M12):** `deploy/backup.sh` writes a timestamped DB + files archive; schedule via the
  systemd timers in [deploy/systemd/README.md](../../deploy/systemd/README.md). Store the restic /
  pgBackRest passphrases off-box — a repo is useless without them.
- **Resource budget (8 GB):** at idle the stack uses ~0.4 GB; under OCR load (models + embedder
  resident) ~2.2 GB of containers, leaving ~4.5 GB free. The per-container limits in the compose are
  ceilings; the real CPU governor is `OCR_CONCURRENCY x OCR_CPU_THREADS <= cores` (2x1 on 4 cores).

## Appendix — the published-image (CI) path

The repo also ships `release.yml` (build + push `doktok-ng-*` to GHCR on a `v*` tag) and `deploy.yml`
(SSH to the box, `compose pull && up -d` an image tag, with a pre-deploy backup + smoke test). To use
it instead of building on the box: push a `v*` tag, set the `DEPLOY_HOST`/`DEPLOY_USER`/
`DEPLOY_SSH_KEY` secrets + a `production` environment, and run the **Deploy** workflow. The box still
needs `/opt/doktok` with `docker-compose.prod.yml` + `.env.production` (steps 2-3).

## Known issues found during the first real deployment (to fix at the image/compose level)

1. **Files volume ownership** — fresh `doktok-files` is root-owned; the unprivileged app can't write
   until a manual `chown` (step 5b). Proper fix: an init step that chowns the volume before
   backend/worker start.
2. *(fixed)* The worker did not receive `DOKTOK_TENANT_TOKENS` (it derives the tenants it watches
   from it) — moved into the shared compose env anchor so both backend and worker get it.
3. *(fixed)* PaddleOCR oneDNN crash on N95 — added `DOKTOK_OCR_ENABLE_MKLDNN` (false on this CPU).
4. *(fixed)* `deploy/pgbackrest/pgbackrest.conf` lacked `pg1-user`/`pg1-database` (the superuser is
   `doktok`, not `postgres`) and writable log/lock paths — stanza-create failed until corrected.

## Last updated

2026-06-25. Procedure validated end-to-end on an Intel N95 / 8 GB / Ubuntu 24.04 box (local-only
profile: OCR + embeddings + search confirmed working).
