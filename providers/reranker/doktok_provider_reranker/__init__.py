"""Local Qwen3-Reranker reranking provider for doktokNG (experimental, opt-in, #466).

A dedicated cross-encoder-style scoring reranker: for each (query, passage) pair the model emits a
yes/no relevance judgement and we rank by the "yes" probability. Far cheaper than the LLM-listwise
reranker and fully on-host. The heavy ``torch`` + ``transformers`` runtime is an optional extra
(``providers/reranker[engine]``, or ``make reranker-models``); importing this package is light, the
model loads only when the reranker is instantiated.
"""

from __future__ import annotations

from .qwen import QwenReranker

__version__ = "0.1.0"

__all__ = ["QwenReranker"]
