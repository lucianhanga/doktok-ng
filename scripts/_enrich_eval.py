"""Local document-enrichment evaluation runner (needs Ollama + DB). Invoked by enrich-eval.sh.

Ingests the golden corpus into a throwaway ``eval`` tenant, runs the doc_metadata + doc_classify
features against the real models, then scores the produced title/date/location/summary/categories
against eval/golden_enrichment.json.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from doktok_core.categories import InMemoryCategoryRepository  # noqa: F401 (kept for parity)
from doktok_core.config import get_settings
from doktok_core.enrichment.evaluation import EnrichCase, EnrichReport, evaluate_enrichment
from doktok_core.features.processors import DocClassifyFeature, DocMetadataFeature
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import (
    DirectTextExtractor,
    LibmagicMimeDetector,
    PyMuPdfTextExtractor,
)
from doktok_provider_ollama import (
    OllamaCategoryClassifier,
    OllamaEmbeddingProvider,
    OllamaMetadataExtractor,
)
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService
from doktok_storage_postgres import (
    Database,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresIngestionJobRepository,
    migrate,
)

TENANT = "eval"
ROOT = Path(__file__).resolve().parent.parent
GREEN, RED, YELLOW, NC = "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0m"


def _clean(db: Database) -> None:
    with db.connection() as conn:
        for table in (
            "document_category_links",
            "categories",
            "document_chunks",
            "documents",
            "ingestion_jobs",
        ):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (TENANT,))


def main() -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    migrate(db)
    _clean(db)

    layout = FilesystemLayout(settings.files_root, TENANT)
    shutil.rmtree(layout.base, ignore_errors=True)
    layout.ensure()
    document_repo = PostgresDocumentRepository(db)
    services = IngestionServices(
        tenant_id=TENANT,
        job_repo=PostgresIngestionJobRepository(db),
        document_repo=document_repo,
        file_storage=LocalFileStorage(),
        hash_service=Sha256HashService(),
        mime_detector=LibmagicMimeDetector(),
        security_policy=DefaultSecurityPolicy(max_file_mb=settings.max_file_mb),
        quarantine_service=QuarantineService(layout),
        text_extractor=DirectTextExtractor(),
        pdf_extractor=PyMuPdfTextExtractor(),
        layout=layout,
        chunker=FixedWindowChunker(),
        embedding_provider=OllamaEmbeddingProvider(
            settings.embedding_model, settings.ollama_base_url
        ),
        chunk_repo=PostgresChunkRepository(db),
    )

    file_storage = LocalFileStorage()
    metadata = DocMetadataFeature(
        document_repo,
        file_storage,
        OllamaMetadataExtractor(
            settings.enrich_model,
            # JSON-repair reuses the same configured model (MoE-safe per the provider).
            settings.enrich_model,
            settings.ollama_base_url,
            think=settings.enrich_think,
        ),
    )
    category_repo = PostgresCategoryRepository(db)
    classify = DocClassifyFeature(
        document_repo,
        file_storage,
        OllamaCategoryClassifier(
            settings.enrich_model,
            # JSON-repair reuses the same configured model (MoE-safe per the provider).
            settings.enrich_model,
            settings.ollama_base_url,
            think=settings.enrich_think,
        ),
        category_repo,
    )

    cases = [
        EnrichCase(**c) for c in json.loads((ROOT / "eval" / "golden_enrichment.json").read_text())
    ]
    by_file = {c.file: c for c in cases}

    corpus = sorted((ROOT / "eval" / "corpus").iterdir())
    print(f"{YELLOW}Ingesting + enriching {len(corpus)} golden documents...{NC}\n")
    results = []
    for path in corpus:
        shutil.copy(path, layout.ingest / path.name)
        job = process_file(services, str(layout.ingest / path.name))
        if job.status.value != "active" or job.document_id is None:
            print(f"{RED}  {path.name} -> {job.status.value}{NC}")
            continue
        metadata.process(TENANT, job.document_id)
        classify.process(TENANT, job.document_id)
        doc = document_repo.get(TENANT, job.document_id)
        cats = [c.name for c in category_repo.list_for_document(TENANT, job.document_id)]
        case = by_file.get(path.name)
        if doc is None or case is None:
            continue
        result = evaluate_enrichment(
            case,
            title=doc.title,
            document_date=doc.document_date,
            location=doc.location,
            summary=doc.summary,
            categories=cats,
        )
        results.append(result)
        mark = f"{GREEN}PASS{NC}" if result.passed else f"{RED}FAIL{NC}"
        print(f"  [{mark}] {path.name}")
        print(f"         title={doc.title!r} date={doc.document_date} loc={doc.location}")
        print(f"         categories={cats}")

    report = EnrichReport(results)
    print(f"\n{YELLOW}=== Summary ==={NC}")
    for key, value in report.summary().items():
        print(f"  {key}: {value}")

    _clean(db)
    shutil.rmtree(layout.base, ignore_errors=True)
    db.close()
    passed = sum(1 for r in results if r.passed)
    color = GREEN if passed == len(results) else YELLOW
    print(f"\n{color}{passed}/{len(results)} documents passed enrichment checks.{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
