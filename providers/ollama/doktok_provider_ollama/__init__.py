"""Ollama chat, embedding, and vision-OCR adapters."""

from doktok_provider_ollama.chat import OllamaChatModelProvider
from doktok_provider_ollama.classify import OllamaCategoryClassifier
from doktok_provider_ollama.embeddings import OllamaEmbeddingProvider
from doktok_provider_ollama.metadata import OllamaMetadataExtractor
from doktok_provider_ollama.ner import OllamaEntityNerExtractor
from doktok_provider_ollama.ocr import OllamaVisionOcr
from doktok_provider_ollama.records import OllamaRecordExtractor

__version__ = "0.1.0"

__all__ = [
    "OllamaCategoryClassifier",
    "OllamaChatModelProvider",
    "OllamaEmbeddingProvider",
    "OllamaEntityNerExtractor",
    "OllamaMetadataExtractor",
    "OllamaRecordExtractor",
    "OllamaVisionOcr",
]
