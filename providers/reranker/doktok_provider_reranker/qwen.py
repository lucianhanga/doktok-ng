"""Qwen3-Reranker cross-encoder reranker (#466).

For each (query, passage) pair the model is asked to answer "yes"/"no" to relevance; the score is
the probability mass on "yes" (log-softmax over the yes/no logits at the final position). Passages
are then ordered by that score. Follows the Qwen3-Reranker model-card usage. torch + transformers
are imported lazily so importing this module stays cheap.
"""

from __future__ import annotations

import logging
from typing import Any

from doktok_contracts.schemas import SearchHit

logger = logging.getLogger("doktok.rag.rerank")

# Model-card framing: a system instruction constrains the answer to yes/no; each pair carries the
# task instruct + query + document.
_PREFIX = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and "
    'the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n'
    "<|im_start|>user\n"
)
_SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
_INSTRUCT = "Given a web search query, retrieve relevant passages that answer the query"
_MAX_DOC_CHARS = 4000  # passages are chunk-sized; cap defensively so one long chunk can't dominate


def _pick_device(device: str | None, torch: Any) -> str:
    if device:
        return device
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class QwenReranker:
    """A ``Reranker`` backed by a local Qwen3-Reranker model. Loads the model on construction; a
    scoring failure falls back to the incoming retrieval order (never raises out of ``rerank``)."""

    def __init__(self, model: str, *, device: str | None = None, max_length: int = 8192) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self._tok = AutoTokenizer.from_pretrained(model, padding_side="left")
        self._model = AutoModelForCausalLM.from_pretrained(model).eval()  # type: ignore[no-untyped-call]
        self._device = _pick_device(device, torch)
        self._model.to(self._device)
        self._max_length = max_length
        self._true_id = self._tok.convert_tokens_to_ids("yes")
        self._false_id = self._tok.convert_tokens_to_ids("no")
        self._prefix_ids = self._tok(_PREFIX, add_special_tokens=False)["input_ids"]
        self._suffix_ids = self._tok(_SUFFIX, add_special_tokens=False)["input_ids"]
        logger.info("reranker backend: local qwen-reranker (%s) on %s", model, self._device)

    def _format(self, query: str, doc: str) -> str:
        return f"<Instruct>: {_INSTRUCT}\n<Query>: {query}\n<Document>: {doc[:_MAX_DOC_CHARS]}"

    def _scores(self, query: str, docs: list[str]) -> list[float]:
        torch = self._torch
        pairs = [self._format(query, d) for d in docs]
        budget = self._max_length - len(self._prefix_ids) - len(self._suffix_ids)
        enc = self._tok(
            pairs,
            padding=False,
            truncation="longest_first",
            add_special_tokens=False,
            max_length=budget,
            return_attention_mask=False,
        )
        enc["input_ids"] = [self._prefix_ids + ids + self._suffix_ids for ids in enc["input_ids"]]
        batch = self._tok.pad(enc, padding=True, return_tensors="pt", max_length=self._max_length)
        batch = batch.to(self._device)
        with torch.no_grad():
            last = self._model(**batch).logits[:, -1, :]
            pair = torch.stack([last[:, self._false_id], last[:, self._true_id]], dim=1)
            yes = torch.nn.functional.log_softmax(pair, dim=1)[:, 1].exp()
            return [float(x) for x in yes.tolist()]

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        if len(hits) <= 1:
            return hits[:top_k]
        try:
            scores = self._scores(query, [h.text or h.snippet for h in hits])
        except Exception:  # noqa: BLE001 - reranking must never break retrieval
            logger.warning("qwen reranker scoring failed; keeping retrieval order", exc_info=True)
            return hits[:top_k]
        order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
        # Attach each hit's calibrated yes-probability so the answerer can use the real score
        # as the citation relevance and apply a min-relevance threshold (rerank_score is None
        # on the no-score fallback paths above, leaving downstream behaviour unchanged).
        return [hits[i].model_copy(update={"rerank_score": scores[i]}) for i in order[:top_k]]
