"""Typed application settings loaded from the environment.

All settings use the ``DOKTOK_`` prefix and have safe, local-first defaults (ADR-0003, ADR-0006).
See .env.example and brief section 24.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from doktok_core.security.egress import is_loopback_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DOKTOK_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "local"
    database_url: str = "postgresql://doktok:doktok@localhost:5432/doktok"
    files_root: str = "./storage/files"

    default_model: str = "qwen3.6:27b"
    # qwen3-embedding (1024-dim) handles >512-token chunks; mxbai-embed-large truncates at 512.
    embedding_model: str = "qwen3-embedding:0.6b"
    # Embedding input is one ~300-token chunk at a time (chunker caps at 1200 chars), so the model's
    # 32k default context just wastes a huge KV cache. 1024 gives ample headroom over a chunk while
    # keeping the (already truncation-free) embeddings byte-identical - no re-index needed.
    embedding_num_ctx: int = 1024
    # Keep the (tiny ~1 GB) embedding model pinned as long as the chat/enrich models, so it is not
    # evicted first and then unable to reload while the 24 GB chat model is pinned - which hangs
    # chunk_embed and stalls the single-threaded reconciler. "-1" pins it forever.
    embedding_keep_alive: str = "30m"
    # OCR engine: "paddleocr" (detect+recognize, native confidence), "rapidocr" (same PP-OCR models
    # via ONNX/OpenVINO - faster+lighter on CPU, M17 #375), or "glm-ocr" (Ollama vision). The paddle
    # extra: `make ocr-paddle`; the rapid extra: `make ocr-rapid`.
    ocr_engine: str = "paddleocr"
    # RapidOCR execution backend when ocr_engine="rapidocr": "onnxruntime" (any CPU) or "openvino"
    # (faster on Intel; needs the rapidocr-openvino extra).
    ocr_rapid_backend: str = "onnxruntime"
    # PaddleOCR recognizer language; 'german' selects the Latin model (German/English/European).
    ocr_lang: str = "german"
    # glm-ocr vision model (used when ocr_engine="glm-ocr"; configurable, ADR-0003).
    ocr_model: str = "glm-ocr:latest"
    # OCR context per single-page call (~4.4k tokens worst case). 32k would waste ~768 MB of KV
    # cache; raise to 16384 only for very dense/multi-column pages. num_predict caps page output.
    ocr_num_ctx: int = 8192
    ocr_num_predict: int = 8192
    ocr_keep_alive: str = "5m"  # OCR runs in bursts; do not pin it resident like the chat model
    # DPI the PDF pages are rasterized at before OCR (and for the searchable-PDF image layer).
    # Measured: 200->120 saves only ~5% (OCR is recognition-bound, not pixel-bound) and can slightly
    # lower confidence, so 200 is the quality-safe default; lower only if you must save RAM.
    ocr_dpi: int = 200
    # Math-library threads PER PaddleOCR worker process. Each worker is ~1 core already, so 1 keeps
    # `ocr_concurrency` workers from oversubscribing: real parallelism comes from the process pool.
    # Rule of thumb: ocr_concurrency * ocr_cpu_threads <= physical cores.
    ocr_cpu_threads: int = 1
    # oneDNN (MKL-DNN) acceleration for PaddleOCR. Default on (faster). Set false on CPUs where
    # PaddlePaddle's oneDNN kernels abort under the PIR executor ("Unimplemented ...
    # onednn_instruction.cc") - notably Intel N95 / Alder Lake-N. DOKTOK_OCR_ENABLE_MKLDNN=false.
    ocr_enable_mkldnn: bool = True
    # Enhanced re-OCR profile (opt-in, slower, better): higher DPI + heavier PP-OCRv6 medium models
    # + the doc-orientation/unwarp/textline-orientation preprocessors (fixes rotated/curved scans).
    # Routed via the ingest.enhanced/ folder; files dropped there use these instead of the defaults.
    ocr_enhanced_dpi: int = 300
    ocr_enhanced_det_model: str = "PP-OCRv6_medium_det"
    ocr_enhanced_rec_model: str = "PP-OCRv6_medium_rec"
    # Gotenberg (local container) converts office documents to PDF on ingest (M8.x #313).
    gotenberg_url: str = "http://localhost:3000"
    ollama_base_url: str = "http://localhost:11434"
    # HTTP timeout (seconds) for each Ollama call. Generous because requests queue at Ollama under
    # parallel ingestion (raise OLLAMA_NUM_PARALLEL to run them concurrently instead of queuing).
    ollama_timeout_seconds: float = 600.0
    # Context window (tokens) requested for the chat/RAG model. 32k suits multi-chunk RAG prompts;
    # qwen3's grouped-query attention keeps the KV cache for this cheap (~1-2 GB). OCR/embeddings
    # are unaffected (they use the model defaults).
    chat_num_ctx: int = 32768
    # Keep the 23 GB RAG model resident so interactive chat doesn't pay a ~14s cold reload after an
    # idle gap. "-1" never evicts; "30m" balances residency against freeing the slot for ingestion.
    chat_keep_alive: str = "30m"
    # RAG: retrieve this many candidates wide; the LLM reranker keeps the best (the chat `limit`).
    rag_retrieve_k: int = 40
    # Deterministic evidence floor: refuse before generating if the best retrieval score is below
    # this (0 = disabled). RRF scores are small (~0.01-0.05); tune against the eval set before use.
    rag_min_score: float = 0.0
    # Minimum reranker relevance [0,1] to keep a source; chunks scoring below this are dropped as
    # off-topic before the answer is composed. Only applied when a cross-encoder reranker ran and
    # scored the hits (rerank_score set); skipped when rerank_score is None on all hits (no reranker
    # or scoring failed). Safety: at least the top-1 hit is always kept regardless of this floor.
    # 0 = disabled (no threshold). Default 0.3 trims clearly off-topic docs while preserving all
    # hits whose relevance is uncertain.
    rerank_min_relevance: float = 0.3
    # Reranker: model for the listwise rerank call (defaults to the chat model; swap to a smaller
    # model to free the chat slot) and a tight output cap (it only emits a short JSON array).
    rerank_model: str = ""  # empty => use default_model
    rerank_num_predict: int = 64
    # Enrichment (M6.2) + the OCR-quality judge are NOT given a hardcoded model: they follow the
    # Data Pipeline AI settings (provider+model) selected in the UI, falling back to default_model
    # only when the pipeline is set to OpenAI but egress is disabled (no UI model to run locally).
    # The enrichment providers feed the document head (up to ~12-16k chars) to the model. 4096
    # tokens was too small for German text (~3 chars/token => ~4-5k tokens), so llama.cpp silently
    # left-truncated the head - exactly the title/date region being extracted. 8192 fits the head
    # with room; the dense 14b KV cache at 8192 is ~1.25 GB and still loads fast.
    enrich_num_ctx: int = 8192
    enrich_think: bool = False
    # Keep the dense enrichment model warm across a batch ingest (avoid a reload per document).
    enrich_keep_alive: str = "30m"
    # A PDF page whose largest image covers >= this fraction of the page is treated as scanned.
    ocr_image_coverage: float = 0.8
    # On such a page, the embedded text layer is kept if its quality score is >= this; otherwise the
    # page is re-OCR'd. Higher = trust OCR more; set to 0 to always keep any embedded text.
    ocr_min_text_quality: float = 0.5
    # Max significant lexemes indexed per document as CUSTOM_TOKEN entities (M5.7, multilingual).
    lexical_terms_limit: int = 200

    # The initial/default no-egress posture. The effective value is the in-app toggle (Settings >
    # AI), which falls back to this when never set. See effective_no_egress().
    no_egress: bool = True
    # Operator hard lock (host env): when true, no-egress is forced on and the in-app toggle is
    # disabled, so a UI user cannot turn off the data-egress guard on a hardened deployment.
    no_egress_lock: bool = False

    # Backup / DRP (M12 #368). The local backup repo dir (status sentinels live in <dir>/status,
    # read by the read-only DRP settings panel + /metrics). azure_* + the *_password presence drive
    # the DRP "configured" booleans (presence only - the values are never returned).
    backup_dir: str = "./backups"
    # Portable one-file backup (M12 portable backup, Phase 1). The backend STAGES the plaintext
    # export here before streaming it out encrypted, so this MUST be on a WRITABLE volume - distinct
    # from backup_dir, which the backend mounts read-only (it only reads the DRP status sentinels
    # there). Empty => default to "<backup_dir>/exports". Staged archives are 0600 and TTL-swept.
    backup_export_dir: str = ""
    # Portable RESTORE upload cap (M12 portable restore Phase 2). The restore preview streams a
    # multi-GB encrypted archive to disk, so it is EXEMPT from the global max_request_mb body-size
    # limit and is instead capped here (reject larger uploads with 413). Generous by default; the
    # archive is the size of db.dump + the whole files_root.
    max_restore_gb: int = 50
    # Deployment topology for backups/DRP (M12 #377): "host" (dev/test run directly on the host) or
    # "compose" (staging/prod containerized). The orchestrator runs backups accordingly; the DRP
    # panel surfaces this so it is honest about what's wired per environment.
    deploy_mode: str = "host"
    azure_container: str = ""
    azure_immutable: bool = False
    restic_password: str = ""
    pgbackrest_cipher_pass: str = ""
    azure_sas: str = ""

    # Logging (APP-12). "json" emits structured logs (request_id + tenant_id correlation, secret
    # redaction) for a log pipeline; "text" is the human-readable dev format. log_level is the root.
    log_format: str = "text"  # 'text' | 'json'
    log_level: str = "INFO"

    # Master key for encrypting secrets at rest (the OpenAI key in app_settings; APP-8). When set,
    # the key is stored Fernet-encrypted and decrypted only in-process; when empty, it is stored as
    # plaintext (local-dev default). Keep this stable - rotating it makes an existing encrypted key
    # undecryptable (re-enter the OpenAI key after changing it).
    secrets_key: str = ""

    # OpenAI API key fallback for headless / bootstrap deploys. Precedence: the key set via the
    # Settings UI (persisted in app_settings) wins; this env var is used only when that DB value is
    # empty. Lets a fresh deployment provision the hybrid (remote pipeline/RAG) split headlessly.
    openai_api_key: str = ""

    # Headless bootstrap of the AI provider split (APP-2). When a provider is set here and NO AI
    # settings have been saved yet (fresh DB), these seed app_settings on startup so a deployment
    # gets the hybrid split without the Settings UI. Empty = leave the stored/default selection. The
    # model is optional (the catalog's default for that provider is used when omitted). Operator
    # edits via the UI are never overwritten (seed-if-absent).
    pipeline_provider: str = ""  # '' | 'ollama' | 'openai'
    pipeline_model: str = ""
    rag_provider: str = ""  # '' | 'ollama' | 'openai'
    rag_model: str = ""

    # Read-only MCP server (ADR-0008). The tenant it serves (must be one of DOKTOK_TENANT_TOKENS'
    # tenants; if empty and exactly one tenant is configured, that one is used). For real read-only
    # enforcement, point mcp_database_url at a Postgres role with only SELECT (defaults to the main
    # DSN); the tool surface is read-only by construction regardless.
    mcp_tenant: str = ""
    mcp_database_url: str = ""

    # Allowed CORS origins (APP-10). Loopback dev origins by default; set to your UI origin(s) for a
    # deployed UI served from another host. JSON list in env, e.g. '["https://docs.example.com"]'.
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Reject request bodies larger than this many MB with 413 (APP-10). The API takes JSON only
    # (documents are ingested via the filesystem watcher, not HTTP upload), so this is generous.
    max_request_mb: int = 25
    # Per-token request rate limit (requests/minute; APP-9). 0 = disabled (default). When set, each
    # token gets a token-bucket of this size, refilled at this rate; /health and /ready are exempt.
    rate_limit_per_minute: int = 0

    # API DB connection pool size. The default Database() pool is small (4); the API runs sync
    # routes in a threadpool and each holds a connection during a (possibly slow) Ollama call, so
    # size it to the expected concurrent request count to avoid pool starvation / blocking.
    api_db_pool_size: int = 10
    # Per-call HTTP timeout (seconds) for the interactive RAG path (chat/search embeddings + rerank
    # + answer). Shorter than the ingestion timeout so a hung model call can't pin an API request
    # and DB connection for the full ingestion budget.
    rag_timeout_seconds: float = 120.0

    # API server bind host (loopback by default; ADR-0008).
    bind_host: str = "127.0.0.1"
    # Bearer token -> tenant_id map (JSON in env; static now, DB-backed later; ADR-0008).
    # Example: DOKTOK_TENANT_TOKENS='{"dev-token-default":"default"}'
    tenant_tokens: dict[str, str] = Field(default_factory=dict)

    max_file_mb: int = 200
    max_pages: int = 500
    file_stability_seconds: int = 3
    # How many stable files the worker pulls through intake/extraction in parallel (1 = sequential).
    # This only orchestrates document flow; it does NOT size the OCR pool. OCR pages from all the
    # in-flight documents share the PaddleOCR pool sized by `ocr_concurrency` below, so OCR
    # parallelism is bounded by that, not by this. Throughput also depends on OLLAMA_NUM_PARALLEL.
    ingest_concurrency: int = 4
    # Size of the PaddleOCR predictor pool = how many pages are OCR'd in parallel across the WHOLE
    # worker (PaddleOCR is CPU-bound, ~1 core per process). Live-reloaded from Settings between
    # ingest scans (no restart). Tune toward the core count, watching CPU/RAM headroom.
    ocr_concurrency: int = 4
    # Staged ingestion (ADR-0015): when on, intake creates a `processing` document + seeds the
    # stage ledger and the `extract` stage does OCR/extraction + activation, instead of the inline
    # pipeline. Default off while the staged path is built and proven; flip once it ships.
    staged_ingestion: bool = False
    # How many feature-reconciler runs proceed in parallel (backfills drain faster). The reconciler
    # claims distinct rows with SKIP LOCKED, so this is safe; bound it by Ollama/DB capacity.
    # Kept low by default because the enrichment features hit the LOCAL Ollama model (a single GPU
    # thrashes under high parallelism).
    reconcile_concurrency: int = 2
    # When the pipeline runs on OpenAI (remote), the enrichment features are network-bound, so the
    # reconciler fans out wider than the local path. Used in place of reconcile_concurrency when the
    # pipeline provider is OpenAI. NOTE: the hard 429 guard is DOKTOK_OPENAI_MAX_CONCURRENCY (a
    # process-wide semaphore in the OpenAI client that also bounds the OCR judge + RAG); this value
    # is just the reconciler's worker count and should be <= that ceiling to avoid idle blocked
    # workers. Kept modest so a small OpenAI rate tier is not overwhelmed out of the box.
    openai_reconcile_concurrency: int = 5
    # A job left in a non-terminal state (extracting/indexing/...) longer than this was abandoned by
    # a killed worker; the worker re-queues it (file back to ingest) so it never lingers invisibly.
    # Keep it above the slowest legitimate single-document extraction. Set to 0 to disable recovery.
    stale_job_minutes: int = 10
    # Insights embedding map (ADR-0016, M7.1): the worker drains projection recompute requests and
    # fits a 2D + 3D projection of the tenant's chunk embeddings. `projection_algorithm` is umap
    # (preferred) or pca; `projection_max_points` caps points per projection (larger tenants are
    # truncated); bump `projection_version` to invalidate every cached projection.
    projection_algorithm: str = "umap"
    projection_max_points: int = 20000
    # PCA pre-reduces 1024D -> projection_pca_components before UMAP (denoises + speeds UMAP, M7.2);
    # HDBSCAN groups the PCA space into clusters (min_cluster_size); n_neighbors tunes UMAP.
    projection_pca_components: int = 50
    projection_min_cluster_size: int = 8
    projection_n_neighbors: int = 15
    # Bump to invalidate every cached projection (the UI then shows them stale, offers Recompute).
    projection_version: int = 2

    @model_validator(mode="after")
    def _enforce_no_egress(self) -> Settings:
        # Make DOKTOK_NO_EGRESS real: with egress off, the only outbound call (Ollama) must target a
        # loopback host. Refuse a remote endpoint at startup instead of silently egressing.
        if self.no_egress and not is_loopback_url(self.ollama_base_url):
            raise ValueError(
                f"DOKTOK_NO_EGRESS is set but DOKTOK_OLLAMA_BASE_URL ({self.ollama_base_url!r}) "
                "is not loopback; point it at localhost or set DOKTOK_NO_EGRESS=false"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
