"""Ollama chat, embedding, and vision-OCR adapters."""

from doktok_provider_ollama.chat import OllamaChatModelProvider
from doktok_provider_ollama.embeddings import OllamaEmbeddingProvider
from doktok_provider_ollama.metadata import OllamaMetadataExtractor
from doktok_provider_ollama.ocr import OllamaVisionOcr

__version__ = "0.0.0"

__all__ = [
    "OllamaChatModelProvider",
    "OllamaEmbeddingProvider",
    "OllamaMetadataExtractor",
    "OllamaVisionOcr",
]
