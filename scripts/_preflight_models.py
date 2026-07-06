#!/usr/bin/env python
"""Emit the LOCAL model-stack resources a service must provision before it starts.

Single source of truth: the selectable model ids come from doktok_core's MODEL_CATALOG
(``settings/catalog.py``) and the configured defaults in ``doktok_core.config.Settings``.
Remote OpenAI options are egress-gated and never pulled - only on-host resources are emitted.

Output: one resource per line, ``<kind> <id>``, where kind is:
  pip     a Makefile target that installs a Python runtime extra (the package list lives in
          the Makefile; it is reused here, never duplicated)
  ollama  an Ollama model to pull (local chat/rag, embedding, or OCR vision model)
  hf      a Hugging Face repo to prefetch (GLiNER NER / GLiNER-Relex / Qwen3-Reranker weights)

Adding a new *local* option to MODEL_CATALOG (or changing an embedding/OCR default in config)
is picked up automatically - there are no hardcoded model ids below.

Usage: _preflight_models.py <backend|worker>
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from doktok_core.settings.catalog import MODEL_CATALOG
from doktok_contracts.schemas import ModelOption

# Per-service Python runtime extras, expressed as the Makefile targets that own their package
# lists. Kept explicit (not catalog-derived) because these extras map to features - OCR engines,
# NER/Relex runtime, the projection engine, the reranker runtime - not to catalog model rows.
PIP_TARGETS: dict[str, tuple[str, ...]] = {
    "worker": ("ocr-paddle", "ocr-rapid", "ner-models", "projection-engine"),
    "backend": ("reranker-models",),
}


def _local_ollama(options: Iterable[ModelOption]) -> list[str]:
    """The Ollama-served (local) models among catalog options; OpenAI (remote) is skipped."""
    return [o.model for o in options if o.provider == "ollama"]


def _local_hf(options: Iterable[ModelOption]) -> list[str]:
    """Non-remote, non-Ollama options: on-host weights fetched from Hugging Face.

    gliner / gliner-relex / qwen-reranker each resolve to a Hugging Face repo id (== ``model``).
    """
    return [o.model for o in options if o.provider not in ("openai", "ollama")]


def _dedup(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _settings():
    """Load configured defaults; a misconfigured .env must not block provisioning."""
    try:
        from doktok_core.config import get_settings

        return get_settings()
    except Exception:
        # Bypass validation and fall back to the field defaults so preflight still knows what to pull.
        from doktok_core.config import Settings

        return Settings.model_construct()


def plan(service: str) -> list[str]:
    if service not in PIP_TARGETS:
        print(
            f"unknown service: {service!r} (expected 'backend' or 'worker')",
            file=sys.stderr,
        )
        raise SystemExit(2)

    s = _settings()
    lines: list[str] = [f"pip {t}" for t in PIP_TARGETS[service]]

    if service == "worker":
        # Ingestion/enrichment: local chat/enrich model(s) + embedding + the OCR vision model.
        ollama = _local_ollama(MODEL_CATALOG.pipeline)
        ollama.append(s.embedding_model)
        ollama.append(s.ocr_model)  # glm-ocr vision engine (used when ocr_engine="glm-ocr")
        # GLiNER NER weights + GLiNER-Relex relation weights.
        hf = _local_hf(MODEL_CATALOG.ner) + _local_hf(MODEL_CATALOG.keg)
    else:  # backend
        # Chat/RAG/search: local chat/rag model(s) + embedding; the reranker weights are HF.
        ollama = _local_ollama(MODEL_CATALOG.rag)
        ollama.append(s.embedding_model)
        hf = _local_hf(MODEL_CATALOG.rerank)

    lines.extend(f"ollama {m}" for m in _dedup(ollama))
    lines.extend(f"hf {r}" for r in _dedup(hf))
    return lines


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: _preflight_models.py <backend|worker>", file=sys.stderr)
        raise SystemExit(2)
    for line in plan(sys.argv[1]):
        print(line)


if __name__ == "__main__":
    main()
