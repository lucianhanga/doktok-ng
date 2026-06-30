"""NER backend benchmark: current LLM extractor vs local GLiNER vs local NuNER.

Compares how the data-enrichment NER step performs across three backends on the labelled
``eval/corpus`` documents (gold in ``eval/golden_entities.json``):

  * current  - the configured pipeline LLM (OpenAI or Ollama, from the live DB AI settings)
  * gliner   - local GLiNER span model (doktok_provider_gliner)
  * nuner    - local NuNER Zero span model (doktok_provider_gliner)

Reports exact and relaxed (token-containment) precision/recall/F1, per-type F1, entities found, and
latency. A backend whose runtime/model/config is unavailable is skipped with a yellow warning rather
than failing the run, so you can benchmark whatever is installed. Invoked by scripts/ner-bench.sh.

Requirements:
  * current  -> `make db` running + the configured provider reachable (Ollama up, or OpenAI key set)
  * gliner/nuner -> `make ner-models` (installs gliner + rapidfuzz; first run downloads the models)
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.ports import EntityNerExtractor

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "eval" / "corpus"
GOLD_FILE = ROOT / "eval" / "golden_entities.json"

# (type, normalized value) is the unit of comparison - offsets differ across backends (the LLM
# reports none), so scoring is on normalized entity strings, not character spans.
Mention = tuple[str, str]


def _norm(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _key(entity_type: str, value: str) -> Mention:
    return (entity_type.upper(), _norm(value))


def _contains(a: str, b: str) -> bool:
    """Token-set containment: one normalized name's tokens are a subset of the other's."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


# --------------------------------------------------------------------------- golden + corpus


@dataclass(frozen=True)
class GoldDoc:
    file: str
    text: str
    mentions: set[Mention]


def load_gold() -> list[GoldDoc]:
    docs: list[GoldDoc] = []
    for case in json.loads(GOLD_FILE.read_text()):
        path = CORPUS / case["file"]
        mentions = {_key(e["type"], e["value"]) for e in case["entities"]}
        docs.append(GoldDoc(file=case["file"], text=path.read_text(), mentions=mentions))
    return docs


# --------------------------------------------------------------------------- scoring


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, tp: int, fp: int, fn: int) -> None:
        self.tp += tp
        self.fp += fp
        self.fn += fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class Report:
    label: str
    exact: Counts = field(default_factory=Counts)
    relaxed: Counts = field(default_factory=Counts)
    per_type: dict[str, Counts] = field(default_factory=dict)
    entities: int = 0
    seconds: float = 0.0
    docs: int = 0

    def score_doc(self, gold: set[Mention], pred: set[Mention]) -> None:
        exact_tp = gold & pred
        g_rem = gold - exact_tp
        p_rem = set(pred - exact_tp)

        # exact
        self.exact.add(len(exact_tp), len(pred - exact_tp), len(gold - exact_tp))
        for etype in {t for t, _ in gold | pred}:
            g_t = {m for m in gold if m[0] == etype}
            p_t = {m for m in pred if m[0] == etype}
            tp = len(g_t & p_t)
            bucket = self.per_type.setdefault(etype, Counts())
            bucket.add(tp, len(p_t) - tp, len(g_t) - tp)

        # relaxed: pair leftover gold to leftover pred of the same type by token containment
        relaxed_tp = len(exact_tp)
        for gtype, gval in sorted(g_rem):
            match = next(
                (m for m in sorted(p_rem) if m[0] == gtype and _contains(gval, m[1])), None
            )
            if match is not None:
                p_rem.discard(match)
                relaxed_tp += 1
        self.relaxed.add(relaxed_tp, len(pred) - relaxed_tp, len(gold) - relaxed_tp)


# --------------------------------------------------------------------------- backends


def _to_mentions(entities: Iterable[ExtractedEntity]) -> set[Mention]:
    return {_key(e.entity_type.value, e.normalized_value or e.entity_text) for e in entities}


def run_backend(report: Report, extractor: EntityNerExtractor, docs: list[GoldDoc]) -> Report:
    for doc in docs:
        start = time.perf_counter()
        entities = extractor.extract(doc.text)
        report.seconds += time.perf_counter() - start
        report.docs += 1
        pred = _to_mentions(entities)
        report.entities += len(pred)
        report.score_doc(doc.mentions, pred)
    return report


def build_current() -> tuple[str, EntityNerExtractor]:
    """The configured pipeline NER extractor, from the live DB-backed AI settings (no fallback)."""
    from doktok_core.config import get_settings
    from doktok_core.security.egress import effective_no_egress
    from doktok_core.settings.catalog import openai_reasoning_effort
    from doktok_provider_ollama import OllamaEntityNerExtractor
    from doktok_provider_openai import OpenAiEntityNerExtractor
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
        return f"current (OpenAI {pl.model})", OpenAiEntityNerExtractor(
            pl.model, key, timeout=settings.rag_timeout_seconds, reasoning_effort=effort
        )
    base = pl.ollama_base_url or settings.ollama_base_url
    return f"current (Ollama {pl.model})", OllamaEntityNerExtractor(
        pl.model, pl.model, base, num_ctx=settings.enrich_num_ctx
    )


def build_gliner() -> tuple[str, EntityNerExtractor]:
    import os

    from doktok_provider_gliner import GlinerEntityNerExtractor

    model = os.environ.get("DOKTOK_NER_MODEL", "gliner-community/gliner_large-v2.5")
    device = os.environ.get("DOKTOK_NER_DEVICE") or None
    return f"gliner ({model})", GlinerEntityNerExtractor(model, device=device)


def build_nuner() -> tuple[str, EntityNerExtractor]:
    import os

    from doktok_provider_gliner import NuNerEntityNerExtractor

    device = os.environ.get("DOKTOK_NER_DEVICE") or None
    return "nuner (numind/NuNER_Zero)", NuNerEntityNerExtractor("numind/NuNER_Zero", device=device)


# --------------------------------------------------------------------------- reporting


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}"


def print_report(reports: list[Report]) -> None:
    print(f"\n{BOLD}NER backend benchmark{RESET}  ({reports[0].docs} docs)\n")
    header = (
        f"{'backend':<28} {'exact P':>8} {'exact R':>8} {'exact F1':>9} "
        f"{'relax F1':>9} {'ents':>6} {'ms/doc':>8}"
    )
    print(BOLD + header + RESET)
    print(DIM + "-" * len(header) + RESET)
    best_f1 = max((r.exact.f1 for r in reports), default=0.0)
    for r in reports:
        ms = (r.seconds / r.docs * 1000) if r.docs else 0.0
        mark = f"{GREEN}*{RESET}" if r.exact.f1 == best_f1 and best_f1 > 0 else " "
        print(
            f"{r.label:<28} {_pct(r.exact.precision):>8} {_pct(r.exact.recall):>8} "
            f"{_pct(r.exact.f1):>9} {_pct(r.relaxed.f1):>9} {r.entities:>6} {ms:>8.1f} {mark}"
        )

    types = sorted({t for r in reports for t in r.per_type})
    if types:
        print(f"\n{BOLD}Per-type exact F1{RESET}")
        sub = f"{'backend':<28}" + "".join(f"{t:>10}" for t in types)
        print(BOLD + sub + RESET)
        print(DIM + "-" * len(sub) + RESET)
        for r in reports:
            row = f"{r.label:<28}"
            for t in types:
                c = r.per_type.get(t)
                row += f"{_pct(c.f1) if c else '   -.- ':>10}"
            print(row)
    print(
        f"\n{DIM}exact = normalized (type,name) match; relax = token-containment match. "
        f"{GREEN}*{RESET}{DIM} = best exact F1.{RESET}\n"
    )


def main() -> int:
    gold = load_gold()
    print(f"{DIM}Loaded {len(gold)} gold docs from {GOLD_FILE.relative_to(ROOT)}{RESET}")

    builders: list[tuple[str, Callable[[], tuple[str, EntityNerExtractor]]]] = [
        ("current", build_current),
        ("gliner", build_gliner),
        ("nuner", build_nuner),
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
            reports.append(run_backend(Report(label=label), extractor, gold))
        except Exception as exc:  # noqa: BLE001 - a backend that errors mid-run is reported, not fatal
            print(f"{RED}error {label}: {exc}{RESET}")

    if not reports:
        print(f"{RED}No backends ran. Start `make db` / the provider, or `make ner-models`.{RESET}")
        return 1
    print_report(reports)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
