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
from doktok_core.entities.extractor import RegexEntityExtractor
from doktok_core.features.processors import (
    EntitiesFeature,
    EntityGraphFeature,
    NerFeature,
    RelationExtractFeature,
)
from doktok_core.indexing.chunker import FixedWindowChunker
from doktok_core.ingestion.layout import FilesystemLayout
from doktok_core.ingestion.pipeline import IngestionServices, process_file
from doktok_core.knowledge_graph.alias import resolve_tenant_aliases
from doktok_core.knowledge_graph.retrieval import DefaultGraphRetriever
from doktok_core.rag.answerer import DefaultRagAnswerer
from doktok_core.rag.evaluation import RagCase, evaluate
from doktok_core.rag.reranker import LlmReranker
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_modalities_files import (
    DirectTextExtractor,
    LibmagicMimeDetector,
    PyMuPdfTextExtractor,
)
from doktok_provider_ollama import (
    OllamaChatModelProvider,
    OllamaEmbeddingProvider,
    OllamaEntityNerExtractor,
    OllamaRelationExtractor,
)
from doktok_retrieval_hybrid import HybridPostgresRetriever
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService
from doktok_storage_postgres import (
    Database,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresIngestionJobRepository,
    PostgresKnowledgeGraphRepository,
    PostgresLexicalTermExtractor,
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


# Every tenant-scoped table the eval run touches, child-first so foreign keys never block a delete.
# Includes the KAG tables (Phase 3): the runner now BUILDS the knowledge graph over the eval tenant.
_EVAL_TABLES = (
    "kg_edge_provenance",
    "kg_edges",
    "kg_entity_aliases",
    "kg_entity_mentions",
    "kg_entities",
    "document_entities",
    "document_features",
    "document_chunks",
    "documents",
    "ingestion_jobs",
)


def _clear_tenant(db: Database) -> None:
    with db.connection() as conn:
        for table in _EVAL_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=%s", (TENANT,))


def _build_knowledge_graph(settings: object, db: Database, document_ids: list[str]) -> None:
    """Build the KAG graph over the eval tenant, mirroring the worker composition's feature chain.

    Runs entities -> ner -> entity_graph -> relations (the real Ollama NER + relation extractors,
    on the pipeline default model) over each ingested document, then folds aliases. The reconciler
    is not running in the eval harness, so the features are driven directly in dependency order.
    """
    entity_repo = PostgresEntityRepository(db)
    kg_repo = PostgresKnowledgeGraphRepository(db)
    document_repo = PostgresDocumentRepository(db)
    file_storage = LocalFileStorage()
    num_ctx = settings.enrich_num_ctx  # type: ignore[attr-defined]
    base_url = settings.ollama_base_url  # type: ignore[attr-defined]
    model = settings.default_model  # type: ignore[attr-defined]
    ner = OllamaEntityNerExtractor(model, model, base_url, num_ctx=num_ctx)
    relation = OllamaRelationExtractor(model, model, base_url, num_ctx=num_ctx)
    features = [
        EntitiesFeature(
            document_repo,
            file_storage,
            RegexEntityExtractor(),
            PostgresLexicalTermExtractor(db),
            entity_repo,
            lexical_terms_limit=settings.lexical_terms_limit,  # type: ignore[attr-defined]
        ),
        NerFeature(document_repo, file_storage, ner, entity_repo),
        EntityGraphFeature(entity_repo, kg_repo),
        RelationExtractFeature(document_repo, file_storage, relation, entity_repo, kg_repo),
    ]
    print(f"{YELLOW}Building the knowledge graph over {len(document_ids)} documents...{NC}")
    for document_id in document_ids:
        for feature in features:
            feature.process(TENANT, document_id)
    resolve_tenant_aliases(kg_repo, TENANT)
    print(
        f"{YELLOW}  KG: {kg_repo.entity_count(TENANT)} entities, "
        f"{kg_repo.edge_count(TENANT)} relations.{NC}"
    )


def main() -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    migrate(db)

    _clear_tenant(db)
    services, layout = _services(settings, db)
    shutil.rmtree(layout.base, ignore_errors=True)
    layout.ensure()

    corpus = sorted((ROOT / "eval" / "corpus").iterdir())
    print(f"{YELLOW}Ingesting {len(corpus)} golden documents into tenant '{TENANT}'...{NC}")
    document_ids: list[str] = []
    for path in corpus:
        shutil.copy(path, layout.ingest / path.name)
        job = process_file(services, str(layout.ingest / path.name))
        if job.status.value != "active":
            print(f"{RED}  {path.name} -> {job.status.value} ({job.error_message}){NC}")
        elif job.document_id:
            document_ids.append(job.document_id)

    # Build the KAG graph so the relational golden cases have edges to traverse (the gap that pure
    # RAG may miss is exactly what the graph-augmented answerer is measured on).
    _build_knowledge_graph(settings, db, document_ids)

    cases = [RagCase(**c) for c in json.loads((ROOT / "eval" / "golden.json").read_text())]
    retriever = HybridPostgresRetriever(
        db, OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_base_url)
    )
    chat = OllamaChatModelProvider(
        settings.default_model, settings.ollama_base_url, num_ctx=settings.chat_num_ctx
    )
    graph_retriever = DefaultGraphRetriever(
        PostgresKnowledgeGraphRepository(db), documents=PostgresDocumentRepository(db)
    )
    answerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=LlmReranker(chat),
        retrieve_k=settings.rag_retrieve_k,
        graph_retriever=graph_retriever,
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

    _clear_tenant(db)
    shutil.rmtree(layout.base, ignore_errors=True)
    db.close()
    passed = int(summary["passed"])  # type: ignore[call-overload]
    total = int(summary["total"])  # type: ignore[call-overload]
    print(f"\n{GREEN if passed == total else YELLOW}{passed}/{total} cases passed.{NC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
