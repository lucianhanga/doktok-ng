"""Knowledge-graph domain logic (KAG): cross-document entity resolution + graph retrieval."""

from doktok_core.knowledge_graph.retrieval import (
    DefaultGraphRetriever,
    link_entities,
    looks_relational,
)

__all__ = [
    "DefaultGraphRetriever",
    "link_entities",
    "looks_relational",
]
