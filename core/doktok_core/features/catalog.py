"""The catalog of reprocessable features (ADR-0009).

These are the features that have a reconciler ``FeatureProcessor`` and can therefore be re-derived
on demand from a document's stored artifacts (the UI exposes them as a "reprocess" dropdown). The
inline ``extract`` step is deliberately absent: it runs once at ingestion and has no processor, so
re-running it means a full re-ingest, not a feature reset.

This is the single source of truth for feature names/versions; the worker registers processors for
the same names and the backend serves this list to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from doktok_core.features.processors import (
    ChunkEmbedFeature,
    DocClassifyFeature,
    DocMetadataFeature,
    EntitiesFeature,
    EntityGraphFeature,
    NerFeature,
    RelationExtractFeature,
    StructuredRecordsFeature,
    ThumbnailFeature,
)


@dataclass(frozen=True)
class FeatureSpec:
    """A reprocessable feature: its stable name/version and human-facing label + description."""

    name: str
    version: int
    label: str
    description: str


@dataclass(frozen=True)
class FeatureGroup:
    """A logical group of KG-related features for badge aggregation and bulk reprocessing.

    ``badge_members`` are the feature names the UI collapses into a single group badge.
    ``reprocess_set`` is the full set of feature names reset when the user triggers a group
    reprocess - may be wider than ``badge_members`` when re-extraction auto-invalidates downstream
    features (e.g. re-running entities/ner invalidates entity_graph and relations).
    """

    id: str
    label: str
    badge_members: tuple[str, ...]
    reprocess_set: tuple[str, ...]


FEATURE_GROUPS: list[FeatureGroup] = [
    FeatureGroup(
        id="entities",
        label="Entities",
        # The UI aggregates the entities + ner badges into one "Entities" group badge.
        badge_members=(EntitiesFeature.name, NerFeature.name),
        # AUTO-CHAIN: re-extracting entities/ner invalidates the graph; reset all four so the
        # reconciler rebuilds entity_graph and relations in dependency order automatically.
        reprocess_set=(
            EntitiesFeature.name,
            NerFeature.name,
            EntityGraphFeature.name,
            RelationExtractFeature.name,
        ),
    ),
    FeatureGroup(
        id="knowledge_graph",
        label="Knowledge graph",
        # The UI aggregates entity_graph + relations into one "Knowledge graph" group badge.
        badge_members=(EntityGraphFeature.name, RelationExtractFeature.name),
        # Reprocessing only the graph tier leaves entity extraction untouched.
        reprocess_set=(EntityGraphFeature.name, RelationExtractFeature.name),
    ),
]

# O(1) lookup by group id; used by the backend router for validation and dispatch.
FEATURE_GROUPS_BY_ID: dict[str, FeatureGroup] = {g.id: g for g in FEATURE_GROUPS}


FEATURE_CATALOG: list[FeatureSpec] = [
    FeatureSpec(
        ChunkEmbedFeature.name,
        ChunkEmbedFeature.version,
        "RAG index",
        "Splits the text into chunks and embeds them for semantic search.",
    ),
    FeatureSpec(
        EntitiesFeature.name,
        EntitiesFeature.version,
        "Entities & keywords",
        "Extracts validated structured entities (IBAN, phone, VAT ID, tax/registration numbers, "
        "addresses) and meaningful keyword tokens.",
    ),
    FeatureSpec(
        NerFeature.name,
        NerFeature.version,
        "People & orgs",
        "Recognises named people, organisations, places and job titles (model-based NER).",
    ),
    FeatureSpec(
        EntityGraphFeature.name,
        EntityGraphFeature.version,
        "Entity graph",
        "Resolves entity mentions into canonical cross-document nodes (knowledge graph).",
    ),
    FeatureSpec(
        RelationExtractFeature.name,
        RelationExtractFeature.version,
        "Relation graph",
        "Extracts directed relation triples between named entities (knowledge graph edges).",
    ),
    FeatureSpec(
        DocMetadataFeature.name,
        DocMetadataFeature.version,
        "Metadata",
        "Generates the title, document date, location and summary.",
    ),
    FeatureSpec(
        DocClassifyFeature.name,
        DocClassifyFeature.version,
        "Categories",
        "Assigns multi-label categories to the document.",
    ),
    FeatureSpec(
        StructuredRecordsFeature.name,
        StructuredRecordsFeature.version,
        "Structured records",
        "Extracts transactions / line items for aggregation queries.",
    ),
    FeatureSpec(
        ThumbnailFeature.name,
        ThumbnailFeature.version,
        "Thumbnail",
        "Renders a first-page preview image for the document card and grid/list views.",
    ),
]
