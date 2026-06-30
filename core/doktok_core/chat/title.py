"""LLM-generated conversation titles (ADR-0022).

A brand-new thread shows no title; after the first question we summarize it into a short title
(a few words) with the chat model. Best-effort: callers fall back to the truncated question when
this returns an empty string.
"""

from __future__ import annotations

import logging

from doktok_contracts.ports import ChatModelProvider

logger = logging.getLogger("doktok.chat.title")

_PROMPT = (
    "You write very short titles for chat conversations. Given the user's question, reply with a "
    "concise title of 3 to 6 words that captures its topic. Do not use quotation marks, do not end "
    "with punctuation, and do not answer the question.\n\n"
    "Question: {question}\n\nTitle:"
)

# Guard rails so a misbehaving model can't store a huge or multi-line "title".
_MAX_LEN = 60


def _clean(raw: str) -> str:
    """Normalize a model's reply into a short single-line title, or '' if unusable."""
    title = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    title = title.strip("\"'").strip()
    # Drop an obvious "Title:" echo and trailing sentence punctuation.
    if title.lower().startswith("title:"):
        title = title[len("title:") :].strip()
    title = title.rstrip(".!?,;:").strip()
    title = " ".join(title.split())  # collapse internal whitespace
    if len(title) > _MAX_LEN:
        title = title[:_MAX_LEN].rstrip()
    return title


def generate_thread_title(model: ChatModelProvider, question: str) -> str:
    """Summarize ``question`` into a short conversation title. Returns '' on any failure (the
    caller keeps the truncated-question fallback). Never raises."""
    q = question.strip()
    if not q:
        return ""
    try:
        return _clean(model.complete(_PROMPT.format(question=q)))
    except Exception:  # best-effort: a title is never worth failing a chat turn over
        logger.debug("thread title generation failed", exc_info=True)
        return ""
