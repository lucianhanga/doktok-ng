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
        "Extracts structured entities (IBAN, dates, money, ...) and meaningful keyword tokens.",
    ),
    FeatureSpec(
        NerFeature.name,
        NerFeature.version,
        "People & orgs",
        "Recognises named people, organisations and places (LLM-assisted NER).",
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
