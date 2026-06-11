"""Local RAG evaluation runner (needs a running Ollama + DB). Invoked by scripts/rag-eval.sh.

Ingests the golden corpus into a throwaway ``eval`` tenant, indexes it with the real embedding
model, then runs the golden Q/A set against the real hybrid retriever + RAG answerer and prints a
report. Aggregation cases (e.g. the Block House total) are expected to *fail* under pure RAG - that
gap is the whole point of measuring.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from doktok_core.config import get_settings
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.rag.answerer import DefaultRagAnswerer
from doktok_core.rag.evaluation import RagCase, evaluate
from doktok_core.rag.reranker import LlmReranker
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import (
    DirectTextExtractor,
    LibmagicMimeDetector,
    PyMuPdfTextExtractor,
)
from doktok_provider_ollama import OllamaChatModelProvider, OllamaEmbeddingProvider
from doktok_retrieval_hybrid import HybridPostgresRetriever
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService
from doktok_storage_postgres import (
    Database,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresIngestionJobRepository,
    migrate,
)

TENANT = "eval"
ROOT = Path(__file__).resolve().parent.parent
GREEN, RED, YELLOW, NC = "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0m"


def _services(settings: object, db: Database) -> tuple[IngestionServices, FilesystemLayout]:
    layout = FilesystemLayout(settings.files_root, TENANT)  # type: ignore[attr-defined]
    layout.ensure()
    services = IngestionServices(
        tenant_id=TENANT,
        job_repo=PostgresIngestionJobRepository(db),
        document_repo=PostgresDocumentRepository(db),
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=LibmagicMimeDetector(),
        security_policy=DefaultSecurityPolicy(max_file_mb=settings.max_file_mb),  # type: ignore[attr-defined]
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        chunker=FixedWindowChunker(),
        embedding_provider=OllamaEmbeddingProvider(
            settings.embedding_model,
            settings.ollama_base_url,  # type: ignore[attr-defined]
        ),
        chunk_repo=PostgresChunkRepository(db),
    )
    return services, layout


def main() -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    migrate(db)

    with db.connection() as conn:
        for table in ("document_chunks", "documents", "ingestion_jobs"):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (TENANT,))
    services, layout = _services(settings, db)
    shutil.rmtree(layout.base, ignore_errors=True)
    layout.ensure()

    corpus = sorted((ROOT / "eval" / "corpus").iterdir())
    print(f"{YELLOW}Ingesting {len(corpus)} golden documents into tenant '{TENANT}'...{NC}")
    for path in corpus:
        shutil.copy(path, layout.ingest / path.name)
        job = process_file(services, str(layout.ingest / path.name))
        if job.status.value != "active":
            print(f"{RED}  {path.name} -> {job.status.value} ({job.error_message}){NC}")

    cases = [RagCase(**c) for c in json.loads((ROOT / "eval" / "golden.json").read_text())]
    retriever = HybridPostgresRetriever(
        db, OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_base_url)
    )
    chat = OllamaChatModelProvider(
        settings.default_model, settings.ollama_base_url, num_ctx=settings.chat_num_ctx
    )
    answerer = DefaultRagAnswerer(
        retriever, chat, reranker=LlmReranker(chat), retrieve_k=settings.rag_retrieve_k
    )

    print(f"{YELLOW}Running {len(cases)} golden cases...{NC}\n")
    report = evaluate(cases, retriever=retriever, answerer=answerer, tenant_id=TENANT)
    for result in report.results:
        mark = f"{GREEN}PASS{NC}" if result.passed else f"{RED}FAIL{NC}"
        print(f"  [{mark}] {result.case.id} ({result.case.kind})")
        print(f"         Q: {result.case.question}")
        print(f"         A: {result.answer[:160]}")

    summary = report.summary()
    print(f"\n{YELLOW}=== Summary ==={NC}")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    with db.connection() as conn:
        for table in ("document_chunks", "documents", "ingestion_jobs"):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (TENANT,))
    shutil.rmtree(layout.base, ignore_errors=True)
    db.close()
    passed = int(summary["passed"])  # type: ignore[call-overload]
    total = int(summary["total"])  # type: ignore[call-overload]
    print(f"\n{GREEN if passed == total else YELLOW}{passed}/{total} cases passed.{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
