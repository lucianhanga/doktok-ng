"""Local RAG evaluation runner (needs a running Ollama + DB). Invoked by scripts/rag-eval.sh.

Ingests the golden corpus into a throwaway ``eval`` tenant, indexes it with the real embedding
model, then runs the golden Q/A set against the real hybrid retriever + RAG answerer and prints a
report. Aggregation cases (e.g. the Block House total) are expected to *fail* under pure RAG - that
gap is the whole point of measuring.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import cast

from doktok_api.orchestration import run_graph
from doktok_contracts.ports import (
    ChatModelProvider,
    EntityNerExtractor,
    RagAnswerer,
    RelationExtractor,
)
from doktok_contracts.schemas import AiSettings, ChatEvent, ChatTurn, RagAnswer
from doktok_core.agent import run_agent
from doktok_core.config import Settings, get_settings
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
from doktok_core.knowledge_graph.evaluation import (
    EdgeTriple,
    ProvenanceInput,
    evaluate_provenance,
    score_edges,
)
from doktok_core.knowledge_graph.retrieval import DefaultGraphRetriever
from doktok_core.rag.answerer import DefaultRagAnswerer
from doktok_core.rag.evaluation import RagCase, evaluate
from doktok_core.rag.reranker import LlmReranker
from doktok_core.security.egress import effective_no_egress, openai_egress_allowed
from doktok_core.security.policy import DefaultSecurityPolicy
from doktok_core.settings.catalog import ollama_think_for, openai_reasoning_effort
from doktok_core.tools import ToolGateway
from doktok_core.tools.library import build_default_registry
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
from doktok_provider_openai import (
    OpenAiChatModelProvider,
    OpenAiEntityNerExtractor,
    OpenAiRelationExtractor,
)
from doktok_retrieval_hybrid import HybridPostgresRetriever
from doktok_storage_filesystem import LocalFileStorage, QuarantineService, Sha256HashService
from doktok_storage_postgres import (
    Database,
    PostgresAppSettingsRepository,
    PostgresCategoryRepository,
    PostgresChunkRepository,
    PostgresDocumentRepository,
    PostgresEntityRepository,
    PostgresIngestionJobRepository,
    PostgresKnowledgeGraphRepository,
    PostgresLexicalTermExtractor,
    PostgresRecordRepository,
    PostgresStatsRepository,
    migrate,
)

TENANT = "eval"
ROOT = Path(__file__).resolve().parent.parent


# The eval runs on EXACTLY the live system AI configuration (Settings > AI, DB-backed): the RAG
# purpose drives chat/answering, the pipeline purpose drives NER/relation extraction. No fallback -
# if a purpose is set to OpenAI but the key/egress isn't usable, the run aborts loudly rather than
# silently substituting a local model, so the benchmark always reflects what production runs.
def _ai_config(settings: Settings, db: Database) -> tuple[AiSettings, str, bool]:
    app = PostgresAppSettingsRepository(db)
    key = app.get_openai_api_key() or settings.openai_api_key
    no_egress = effective_no_egress(
        app.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    return app.get_ai_settings(), key, no_egress


def _require_openai(purpose: str, key: str, no_egress: bool) -> None:
    if not openai_egress_allowed(key=key, no_egress=no_egress):
        why = "no-egress is on" if no_egress else "no OpenAI key is configured"
        raise SystemExit(
            f"{RED}{purpose} is configured for OpenAI but {why}. Fix Settings > AI or egress - the "
            f"eval uses only what is configured (no fallback).{NC}"
        )


def _build_chat_model(
    settings: Settings, ai: AiSettings, key: str, no_egress: bool
) -> tuple[ChatModelProvider, str]:
    """The chat/answer model from the configured RAG purpose - OpenAI or Ollama, no fallback."""
    rag = ai.rag
    if rag.provider == "openai":
        _require_openai("Document interrogation (RAG)", key, no_egress)
        return (
            OpenAiChatModelProvider(
                rag.model,
                key,
                timeout=settings.rag_timeout_seconds,
                reasoning_effort=openai_reasoning_effort(rag.reasoning, rag.model),
            ),
            f"OpenAI {rag.model}",
        )
    return (
        OllamaChatModelProvider(
            rag.model,
            rag.ollama_base_url or settings.ollama_base_url,
            timeout=settings.rag_timeout_seconds,
            num_ctx=rag.num_ctx,
            think=ollama_think_for(rag.reasoning, rag.model, structured=False),
        ),
        f"Ollama {rag.model}",
    )


def _build_extractors(
    settings: Settings, ai: AiSettings, key: str, no_egress: bool
) -> tuple[EntityNerExtractor, RelationExtractor, str]:
    """The NER + relation extractors from the configured pipeline purpose - no fallback."""
    pl = ai.pipeline
    if pl.provider == "openai":
        _require_openai("Data pipeline", key, no_egress)
        effort = openai_reasoning_effort(pl.reasoning, pl.model)
        ner = OpenAiEntityNerExtractor(
            pl.model, key, timeout=settings.rag_timeout_seconds, reasoning_effort=effort
        )
        relation = OpenAiRelationExtractor(
            pl.model, key, timeout=settings.rag_timeout_seconds, reasoning_effort=effort
        )
        return ner, relation, f"OpenAI {pl.model}"
    base = pl.ollama_base_url or settings.ollama_base_url
    ner_o = OllamaEntityNerExtractor(pl.model, pl.model, base, num_ctx=settings.enrich_num_ctx)
    rel_o = OllamaRelationExtractor(pl.model, pl.model, base, num_ctx=settings.enrich_num_ctx)
    return ner_o, rel_o, f"Ollama {pl.model}"


# Which chat path to evaluate (ADR-0022): "classic" (default - the deterministic RAG answerer),
# "agent" (single-agent tool loop) or "multi" (the LangGraph graph). Set DOKTOK_EVAL_CHAT_MODE to
# benchmark whether the agent paths actually beat classic on the golden set.
def _chat_mode() -> str:
    mode = os.environ.get("DOKTOK_EVAL_CHAT_MODE", "classic").lower()
    return mode if mode in ("classic", "agent", "multi") else "classic"


class _AgentAnswerer:
    """Adapts the agent/multi chat paths to the ``RagAnswerer`` interface the evaluator calls, so
    the golden set is scored through them with the same metrics as classic RAG."""

    def __init__(
        self, mode: str, *, model: object, gateway: ToolGateway, tool_specs: object
    ) -> None:
        self._mode = mode
        self._model = model
        self._gateway = gateway
        self._specs = tool_specs

    def _run(self, tenant_id: str, history: list[ChatTurn], question: str) -> RagAnswer:
        runner = run_graph if self._mode == "multi" else run_agent
        return runner(
            tenant_id,
            question,
            model=self._model,  # type: ignore[arg-type]
            gateway=self._gateway,
            tool_specs=self._specs,  # type: ignore[arg-type]
            history=history,
        )

    def answer(self, tenant_id: str, question: str, limit: int = 8) -> RagAnswer:
        return self._run(tenant_id, [], question)

    def answer_thread(
        self, tenant_id: str, history: list[ChatTurn], question: str, limit: int = 8
    ) -> RagAnswer:
        return self._run(tenant_id, history, question)

    def answer_thread_stream(
        self,
        tenant_id: str,
        history: list[ChatTurn],
        question: str,
        limit: int = 8,
        *,
        reasoning: bool | None = None,
    ):  # noqa: ANN201 - generator, not used by the evaluator (kept for protocol completeness)
        answer = self._run(tenant_id, history, question)
        yield ChatEvent(type="token", delta=answer.answer)
        yield ChatEvent(type="done", grounded=answer.grounded)


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


def _build_knowledge_graph(
    settings: object,
    db: Database,
    document_ids: list[str],
    ner: EntityNerExtractor,
    relation: RelationExtractor,
) -> None:
    """Build the KAG graph over the eval tenant, mirroring the worker composition's feature chain.

    Runs entities -> ner -> entity_graph -> relations using the NER + relation extractors built from
    the configured pipeline purpose (see _build_extractors - OpenAI or Ollama, no fallback) over
    each document, then folds aliases. The reconciler isn't running in the eval harness, so the
    features are driven directly in dependency order.
    """
    entity_repo = PostgresEntityRepository(db)
    kg_repo = PostgresKnowledgeGraphRepository(db)
    document_repo = PostgresDocumentRepository(db)
    file_storage = LocalFileStorage()
    features = [
        EntitiesFeature(
            document_repo,
            file_storage,
            RegexEntityExtractor(),
            PostgresLexicalTermExtractor(db),
            entity_repo,
            lexical_terms_limit=settings.lexical_terms_limit,  # type: ignore[attr-defined]
        ),
        NerFeature(document_repo, file_storage, lambda _t: ner, entity_repo),
        EntityGraphFeature(entity_repo, kg_repo),
        RelationExtractFeature(
            document_repo, file_storage, lambda _t: relation, entity_repo, kg_repo
        ),
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


def _load_graph(db: Database) -> tuple[list[EdgeTriple], list[ProvenanceInput]]:
    """Read the just-built eval-tenant graph (no rebuild): the distinct edges (as labelled triples)
    and one provenance row per evidence, each paired with its source document's text."""
    corpus_text: dict[str, str] = {}

    def _doc_text(filename: str) -> str:
        if filename not in corpus_text:
            path = ROOT / "eval" / "corpus" / filename
            corpus_text[filename] = path.read_text() if path.exists() else ""
        return corpus_text[filename]

    with db.connection() as conn:
        edge_rows = conn.execute(
            "SELECT e.id, s.normalized_value, e.predicate, o.normalized_value "
            "FROM kg_edges e "
            "JOIN kg_entities s ON s.id = e.src_entity_id AND s.tenant_id = e.tenant_id "
            "JOIN kg_entities o ON o.id = e.dst_entity_id AND o.tenant_id = e.tenant_id "
            "WHERE e.tenant_id = %s",
            (TENANT,),
        ).fetchall()
        prov_rows = conn.execute(
            "SELECT p.edge_id, p.document_id, p.evidence, d.original_filename "
            "FROM kg_edge_provenance p "
            "JOIN documents d ON d.id = p.document_id AND d.tenant_id = p.tenant_id "
            "WHERE p.tenant_id = %s",
            (TENANT,),
        ).fetchall()

    edges = {row[0]: (row[1], row[2], row[3]) for row in edge_rows}
    # First-seen source filename per edge, for the (informational) source field on the triple.
    edge_source: dict[str, str] = {}
    for edge_id, _doc_id, _evidence, filename in prov_rows:
        edge_source.setdefault(edge_id, filename)

    extracted = [
        EdgeTriple(subject=s, predicate=p, object=o, source=edge_source.get(edge_id, ""))
        for edge_id, (s, p, o) in edges.items()
    ]
    provenance = [
        ProvenanceInput(
            edge=EdgeTriple(
                subject=edges[edge_id][0],
                predicate=edges[edge_id][1],
                object=edges[edge_id][2],
                source=filename,
            ),
            document_id=document_id,
            evidence=evidence,
            document_text=_doc_text(filename),
        )
        for edge_id, document_id, evidence, filename in prov_rows
        if edge_id in edges
    ]
    return extracted, provenance


def _fmt_triple(triple: EdgeTriple) -> str:
    src = f" [{triple.source}]" if triple.source else ""
    return f"{triple.subject} {triple.predicate} {triple.object}{src}"


def _score_graph(db: Database) -> None:
    """Print the two graph-quality sections (edge P/R/F1 + provenance correctness) over the already
    -built eval-tenant graph. Measurement only - it never rebuilds or mutates the graph."""
    extracted, provenance = _load_graph(db)
    gold = [EdgeTriple(**g) for g in json.loads((ROOT / "eval" / "golden_edges.json").read_text())]

    edges_report = score_edges(extracted, gold)
    ov = edges_report.overall
    print(f"\n{YELLOW}=== KG edge quality ==={NC}")
    color = GREEN if ov.f1 == 1.0 else (RED if ov.f1 == 0.0 else YELLOW)
    print(
        f"  {color}overall: P={ov.precision} R={ov.recall} F1={ov.f1}{NC} "
        f"(TP={ov.true_positives}, gold={ov.gold_total}, extracted={ov.extracted_total})"
    )
    for predicate, sc in edges_report.per_predicate.items():
        print(
            f"    {predicate}: P={sc.precision} R={sc.recall} F1={sc.f1} "
            f"(TP={sc.true_positives}, gold={sc.gold_total}, extracted={sc.extracted_total})"
        )
    if edges_report.missed_gold:
        print(f"  {RED}missed gold edges ({len(edges_report.missed_gold)}):{NC}")
        for triple in edges_report.missed_gold:
            print(f"    - {_fmt_triple(triple)}")
    if edges_report.spurious:
        print(f"  {YELLOW}spurious extracted edges ({len(edges_report.spurious)}):{NC}")
        for triple in edges_report.spurious:
            print(f"    - {_fmt_triple(triple)}")

    prov_report = evaluate_provenance(provenance)
    pct = round(prov_report.rate * 100, 1)
    color = GREEN if prov_report.rate == 1.0 else (RED if prov_report.rate == 0.0 else YELLOW)
    print(f"\n{YELLOW}=== Provenance correctness ==={NC}")
    print(
        f"  {color}{prov_report.valid}/{prov_report.total} edges have valid evidence ({pct}%){NC}"
    )
    for check in prov_report.invalid:
        print(f"    - {_fmt_triple(check.edge)} -> {check.reason}")


def main() -> int:
    settings = get_settings()
    db = Database(settings.database_url)
    migrate(db)

    # Build EVERYTHING from the live system AI configuration (no fallback). Fails loudly here if a
    # purpose is set to OpenAI but unusable, before any work is done.
    ai, key, no_egress = _ai_config(settings, db)
    ner, relation, extract_desc = _build_extractors(settings, ai, key, no_egress)
    chat, chat_desc = _build_chat_model(settings, ai, key, no_egress)
    mode = _chat_mode()
    print(f"{YELLOW}=== Eval environment ==={NC}")
    print(f"  extraction (pipeline): {extract_desc}")
    print(f"  chat (interrogation):  {chat_desc}")
    print(f"  embedding:             Ollama {settings.embedding_model}")
    print(f"  no-egress:             {no_egress}")
    print(f"  chat mode:             {mode}")
    print(f"  tenant:                {TENANT}\n")

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
    _build_knowledge_graph(settings, db, document_ids, ner, relation)
    # Graph-quality tracks: score the just-built graph (edge P/R/F1 + provenance) - no rebuild.
    _score_graph(db)

    cases = [RagCase(**c) for c in json.loads((ROOT / "eval" / "golden.json").read_text())]
    retriever = HybridPostgresRetriever(
        db, OllamaEmbeddingProvider(settings.embedding_model, settings.ollama_base_url)
    )
    graph_retriever = DefaultGraphRetriever(
        PostgresKnowledgeGraphRepository(db), documents=PostgresDocumentRepository(db)
    )
    answerer: RagAnswerer = DefaultRagAnswerer(
        retriever,
        chat,
        reranker=LlmReranker(chat),
        retrieve_k=settings.rag_retrieve_k,
        graph_retriever=graph_retriever,
    )

    # Agent/multi modes (ADR-0022): route every golden case through the tool-calling loop or the
    # multi-agent graph instead of classic RAG, scored with the identical metrics for comparison.
    if mode in ("agent", "multi"):
        registry = build_default_registry(
            documents=PostgresDocumentRepository(db),
            entities=PostgresEntityRepository(db),
            retriever=retriever,
            records=PostgresRecordRepository(db),
            graph_retriever=graph_retriever,
            stats=PostgresStatsRepository(db),
            categories=PostgresCategoryRepository(db),
        )
        answerer = cast(
            RagAnswerer,
            _AgentAnswerer(
                mode, model=chat, gateway=ToolGateway(registry), tool_specs=registry.specs()
            ),
        )

    print(f"{YELLOW}Running {len(cases)} golden cases (chat mode: {mode})...{NC}\n")
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
