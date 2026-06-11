"""Typed application settings loaded from the environment.

All settings use the ``DOKTOK_`` prefix and have safe, local-first defaults (ADR-0003, ADR-0006).
See .env.example and brief section 24.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    default_model: str = "qwen3.6:35b-a3b"
    # qwen3-embedding (1024-dim) handles >512-token chunks; mxbai-embed-large truncates at 512.
    embedding_model: str = "qwen3-embedding:0.6b"
    # OCR engine: "paddleocr" (detect+recognize, no repeat-loops, native confidence) or "glm-ocr"
    # (Ollama vision model). PaddleOCR needs its extra: uv pip install paddleocr paddlepaddle.
    ocr_engine: str = "paddleocr"
    # PaddleOCR recognizer language; 'german' selects the Latin model (German/English/European).
    ocr_lang: str = "german"
    # glm-ocr vision model (used when ocr_engine="glm-ocr"; configurable, ADR-0003).
    ocr_model: str = "glm-ocr:latest"
    # OCR context per single-page call (~4.4k tokens worst case). 32k would waste ~768 MB of KV
    # cache; raise to 16384 only for very dense/multi-column pages. num_predict caps page output.
    ocr_num_ctx: int = 8192
    ocr_num_predict: int = 8192
    ocr_keep_alive: str = "5m"  # OCR runs in bursts; do not pin it resident like the chat model
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
    # Reranker: model for the listwise rerank call (defaults to the chat model; swap to a smaller
    # model to free the chat slot) and a tight output cap (it only emits a short JSON array).
    rerank_model: str = ""  # empty => use default_model
    rerank_num_predict: int = 64
    # Enrichment (M6.2): primary extraction model (structured JSON; thinking on, never think=false
    # with format) and a small dense fallback used only to repair invalid JSON into the schema.
    # Dense default: think=false + structured `format` works reliably on qwen3:14b and is far faster
    # than the qwen3.6 MoE for extraction (when kept warm). Switch to qwen3.6:35b-a3b +
    # DOKTOK_ENRICH_THINK=true for higher quality/language fidelity at the cost of latency.
    # OCR-quality judge (embedded-text vs OCR). Defaults to the dense enrichment model so ingestion
    # never needs the 23 GB qwen3.6 (which would evict qwen3:14b on a ~48 GB box). Small context: it
    # only compares a page of text. RAG chat still uses DOKTOK_DEFAULT_MODEL.
    judge_model: str = "qwen3:14b"
    judge_num_ctx: int = 8192
    enrich_model: str = "qwen3:14b"
    enrich_repair_model: str = "qwen3:14b"
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

    no_egress: bool = True

    # API server bind host (loopback by default; ADR-0008).
    bind_host: str = "127.0.0.1"
    # Bearer token -> tenant_id map (JSON in env; static now, DB-backed later; ADR-0008).
    # Example: DOKTOK_TENANT_TOKENS='{"dev-token-default":"default"}'
    tenant_tokens: dict[str, str] = Field(default_factory=dict)

    max_file_mb: int = 200
    max_pages: int = 500
    file_stability_seconds: int = 3
    # How many stable files the worker processes in parallel (1 = sequential). Throughput gains
    # depend on Ollama parallelism (OLLAMA_NUM_PARALLEL); 2 keeps OCR+embedding+enrichment from
    # thrashing Ollama's memory on a single ~48 GB box (raise once it's proven stable).
    ingest_concurrency: int = 2
    # How many feature-reconciler runs proceed in parallel (backfills drain faster). The reconciler
    # claims distinct rows with SKIP LOCKED, so this is safe; bound it by Ollama/DB capacity.
    reconcile_concurrency: int = 2
    # A job left in a non-terminal state (extracting/indexing/...) longer than this was abandoned by
    # a killed worker; the worker re-queues it (file back to ingest) so it never lingers invisibly.
    # Keep it above the slowest legitimate single-document extraction. Set to 0 to disable recovery.
    stale_job_minutes: int = 10


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
