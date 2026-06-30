"""KG relation-extraction benchmark: current LLM relations vs local GLiNER-Relex.

Compares the relation-extraction step of KAG enrichment across backends on the labelled
``eval/corpus`` (gold edges in ``eval/golden_edges.json``). To isolate relation quality from NER
quality, BOTH backends are grounded on the SAME gold entities (``eval/golden_entities.json``) - the
exact ``entity_list`` doktok's RelationExtractFeature would pass in.

  * current      - the configured pipeline LLM relation extractor (OpenAI or Ollama, live DB config)
  * gliner-relex - local GLiNER-Relex joint extractor (doktok_provider_gliner)

Scored with the same edge metric the KAG eval uses (``score_edges``): normalized
``(subject, predicate, object)`` match, overall + per-predicate P/R/F1, plus latency and counts.
Unavailable backends are skipped with a yellow warning. Invoked by scripts/kg-bench.sh.

Requirements:
  * current      -> `make db` running + the configured provider reachable (Ollama up / OpenAI key)
  * gliner-relex -> `make ner-models` (installs gliner; first run downloads the relex model)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from doktok_contracts.media import ExtractedRelation
from doktok_contracts.ports import RelationExtractor
from doktok_core.knowledge_graph.evaluation import EdgeTriple, score_edges

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "eval" / "corpus"
GOLD_EDGES = ROOT / "eval" / "golden_edges.json"
GOLD_ENTITIES = ROOT / "eval" / "golden_entities.json"


@dataclass(frozen=True)
class Doc:
    file: str
    text: str
    entity_list: list[tuple[str, str]]  # (name, type) grounding passed to the extractor


def load_docs() -> list[Doc]:
    docs: list[Doc] = []
    for case in json.loads(GOLD_ENTITIES.read_text()):
        path = CORPUS / case["file"]
        entity_list = [(e["value"], e["type"]) for e in case["entities"]]
        docs.append(Doc(file=case["file"], text=path.read_text(), entity_list=entity_list))
    return docs


def load_gold_edges() -> list[EdgeTriple]:
    return [EdgeTriple(**g) for g in json.loads(GOLD_EDGES.read_text())]


@dataclass
class Report:
    label: str
    extracted: list[EdgeTriple] = field(default_factory=list)
    seconds: float = 0.0
    docs: int = 0


def _to_edges(relations: list[ExtractedRelation], source: str) -> list[EdgeTriple]:
    return [
        EdgeTriple(subject=r.subject, predicate=r.predicate, object=r.object, source=source)
        for r in relations
    ]


def run_backend(report: Report, extractor: RelationExtractor, docs: list[Doc]) -> Report:
    for doc in docs:
        if not doc.entity_list:
            report.docs += 1
            continue
        start = time.perf_counter()
        relations = extractor.extract(doc.text, doc.entity_list)
        report.seconds += time.perf_counter() - start
        report.docs += 1
        report.extracted.extend(_to_edges(relations, doc.file))
    return report


def build_current() -> tuple[str, RelationExtractor]:
    """The configured pipeline relation extractor, from the live DB-backed AI settings."""
    from doktok_core.config import get_settings
    from doktok_core.security.egress import effective_no_egress
    from doktok_core.settings.catalog import openai_reasoning_effort
    from doktok_provider_ollama import OllamaRelationExtractor
    from doktok_provider_openai import OpenAiRelationExtractor
    from doktok_storage_postgres import Database, PostgresAppSettingsRepository

    settings = get_settings()
    db = Database(settings.database_url)
    app = PostgresAppSettingsRepository(db)
    ai = app.get_ai_settings()
    pl = ai.pipeline
    key = app.get_openai_api_key() or settings.openai_api_key
    no_egress = effective_no_egress(
        app.get_no_egress(), env_default=settings.no_egress, lock=settings.no_egress_lock
    )
    if pl.provider == "openai":
        if not key or no_egress:
            raise RuntimeError("pipeline is OpenAI but no usable key / egress is off")
        effort = openai_reasoning_effort(pl.reasoning, pl.model)
        return f"current (OpenAI {pl.model})", OpenAiRelationExtractor(
            pl.model, key, timeout=settings.rag_timeout_seconds, reasoning_effort=effort
        )
    base = pl.ollama_base_url or settings.ollama_base_url
    return f"current (Ollama {pl.model})", OllamaRelationExtractor(
        pl.model, pl.model, base, num_ctx=settings.enrich_num_ctx
    )


def build_gliner_relex() -> tuple[str, RelationExtractor]:
    import os

    from doktok_provider_gliner import GlinerRelexRelationExtractor

    model = os.environ.get("DOKTOK_REL_MODEL", "knowledgator/gliner-relex-large-v1.0")
    device = os.environ.get("DOKTOK_NER_DEVICE") or None
    return f"gliner-relex ({model})", GlinerRelexRelationExtractor(model, device=device)


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}"


def print_report(reports: list[Report], gold: list[EdgeTriple]) -> None:
    scored = [(r, score_edges(r.extracted, gold)) for r in reports]
    print(f"\n{BOLD}KG relation benchmark{RESET}  ({reports[0].docs} docs, {len(gold)} gold)\n")
    header = f"{'backend':<32} {'P':>6} {'R':>6} {'F1':>6} {'edges':>6} {'ms/doc':>8}"
    print(BOLD + header + RESET)
    print(DIM + "-" * len(header) + RESET)
    best_f1 = max((s.overall.f1 for _, s in scored), default=0.0)
    for r, s in scored:
        ms = (r.seconds / r.docs * 1000) if r.docs else 0.0
        mark = f"{GREEN}*{RESET}" if s.overall.f1 == best_f1 and best_f1 > 0 else " "
        print(
            f"{r.label:<32} {_pct(s.overall.precision):>6} {_pct(s.overall.recall):>6} "
            f"{_pct(s.overall.f1):>6} {len(r.extracted):>6} {ms:>8.1f} {mark}"
        )

    predicates = sorted({p for _, s in scored for p in s.per_predicate})
    if predicates:
        print(f"\n{BOLD}Per-predicate F1{RESET}")
        sub = f"{'backend':<32}" + "".join(f"{p[:13]:>14}" for p in predicates)
        print(BOLD + sub + RESET)
        print(DIM + "-" * len(sub) + RESET)
        for r, s in scored:
            row = f"{r.label:<32}"
            for p in predicates:
                cell = s.per_predicate.get(p)
                row += f"{(_pct(cell.f1) if cell else '   -.-'):>14}"
            print(row)

    # Diagnostics for the best backend: what it missed and what it over-produced.
    for r, s in scored:
        if s.overall.f1 == best_f1 and best_f1 > 0:
            if s.missed_gold:
                miss = ", ".join(f"{e.subject}-{e.predicate}->{e.object}" for e in s.missed_gold)
                print(f"\n{YELLOW}missed by {r.label}: {miss}{RESET}")
            if s.spurious:
                extra = ", ".join(f"{e.subject}-{e.predicate}->{e.object}" for e in s.spurious[:8])
                print(f"{DIM}spurious from {r.label}: {extra}{RESET}")
            break
    legend = f"{DIM}match = normalized (subject, predicate, object).{RESET}"
    print(f"\n{legend} {GREEN}*{RESET}{DIM} best F1{RESET}\n")


def main() -> int:
    docs = load_docs()
    gold = load_gold_edges()
    print(f"{DIM}Loaded {len(docs)} docs, {len(gold)} gold edges{RESET}")

    builders: list[tuple[str, Callable[[], tuple[str, RelationExtractor]]]] = [
        ("current", build_current),
        ("gliner-relex", build_gliner_relex),
    ]
    reports: list[Report] = []
    for name, builder in builders:
        try:
            label, extractor = builder()
        except Exception as exc:  # noqa: BLE001 - skip unavailable backends, keep benchmarking
            print(f"{YELLOW}skip {name}: {exc}{RESET}")
            continue
        print(f"{DIM}running {label} ...{RESET}")
        try:
            reports.append(run_backend(Report(label=label), extractor, docs))
        except Exception as exc:  # noqa: BLE001 - a backend that errors mid-run is reported, not fatal
            print(f"{RED}error {label}: {exc}{RESET}")

    if not reports:
        print(f"{RED}No backends ran. Start `make db` / the provider, or `make ner-models`.{RESET}")
        return 1
    print_report(reports, gold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
