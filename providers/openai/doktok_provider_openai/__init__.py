"""OpenAI chat + structured-extraction adapters."""

from doktok_provider_openai.chat import OpenAiChatModelProvider
from doktok_provider_openai.classify import OpenAiCategoryClassifier
from doktok_provider_openai.client import (
    OpenAiAuthError,
    OpenAiError,
    OpenAiRateLimitError,
    OpenAiServerError,
    OpenAiTimeoutError,
)
from doktok_provider_openai.metadata import OpenAiMetadataExtractor
from doktok_provider_openai.ner import OpenAiEntityNerExtractor
from doktok_provider_openai.records import OpenAiRecordExtractor
from doktok_provider_openai.relations import OpenAiRelationExtractor

__version__ = "0.2.0"

__all__ = [
    "OpenAiAuthError",
    "OpenAiCategoryClassifier",
    "OpenAiChatModelProvider",
    "OpenAiEntityNerExtractor",
    "OpenAiError",
    "OpenAiMetadataExtractor",
    "OpenAiRateLimitError",
    "OpenAiRecordExtractor",
    "OpenAiRelationExtractor",
    "OpenAiServerError",
    "OpenAiTimeoutError",
]
