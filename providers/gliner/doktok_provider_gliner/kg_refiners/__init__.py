"""kg_refiners: KAG / KG enrichment library.

Use GLiNER-Relex for open-source joint entity+relation extraction, then refine
outputs into graph-ready triples with canonical entity IDs and provenance.
"""

from .config import (
    EntityLinkingConfig,
    KAGEnrichmentConfig,
    RelationRefinementConfig,
    RelexModelConfig,
    RuleRelationConfig,
)
from .entity_linker import CanonicalEntity, EntityLinker
from .extractors import BaseKGExtractor, GLiNERRelexExtractor, RuleRelationExtractor
from .fallback import FallbackAdapter
from .graph_writer import CypherGraphWriter, JsonlGraphWriter, NetworkXGraphWriter
from .pipeline import KAGEnricher
from .schemas import (
    EnrichmentResult,
    EntityMention,
    KGTriple,
    LowConfidenceItem,
    RelationMention,
    RelationSchemaRule,
    TextChunk,
)
from .triple_refiner import TripleRefiner

__all__ = [
    "KAGEnricher",
    "EntityLinkingConfig",
    "KAGEnrichmentConfig",
    "RelationRefinementConfig",
    "RelexModelConfig",
    "RuleRelationConfig",
    "CanonicalEntity",
    "EntityLinker",
    "BaseKGExtractor",
    "GLiNERRelexExtractor",
    "RuleRelationExtractor",
    "FallbackAdapter",
    "TripleRefiner",
    "JsonlGraphWriter",
    "CypherGraphWriter",
    "NetworkXGraphWriter",
    "TextChunk",
    "EntityMention",
    "RelationMention",
    "KGTriple",
    "LowConfidenceItem",
    "EnrichmentResult",
    "RelationSchemaRule",
]
