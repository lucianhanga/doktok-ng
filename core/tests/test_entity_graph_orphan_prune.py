"""entity_graph reprocess prunes nodes orphaned by re-extraction (#528 follow-up, Bug 1).

When re-extraction changes what a document produces - e.g. the PLZ-split (#528) replaces a fused
"80287 München" GPE mention with a separate city + postal-code pair - the old fused node loses its
last mention. ``upsert_entities`` never deletes, so before this fix the orphan lingered and the
graph kept drawing it. entity_graph now prunes tenant-wide orphans after rebuilding a doc.
"""

from __future__ import annotations

import uuid

from doktok_contracts.schemas import DocumentEntity, EntityType
from doktok_core.entities.inmemory import InMemoryEntityRepository
from doktok_core.features.processors import EntityGraphFeature
from doktok_core.knowledge_graph.inmemory import InMemoryKnowledgeGraphRepository
from doktok_core.knowledge_graph.resolve import canonical_entity_id

TENANT = "t1"


def _mention(document_id: str, value: str, etype: EntityType) -> DocumentEntity:
    return DocumentEntity(
        id=uuid.uuid4().hex,
        tenant_id=TENANT,
        document_id=document_id,
        version_id="",
        entity_text=value,
        entity_type=etype,
        normalized_value=value,
    )


def _node_values(kg: InMemoryKnowledgeGraphRepository, etype: EntityType) -> set[str]:
    return {
        e.normalized_value
        for e in kg.list_entities(TENANT)
        if e.entity_type is etype and e.normalized_value
    }


def test_reprocess_prunes_node_orphaned_by_reextraction() -> None:
    entities = InMemoryEntityRepository()
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)

    # First pass: the fused GPE "80287 münchen" (pre-#528 shape).
    entities.add_entities([_mention("d1", "80287 münchen", EntityType.GPE)])
    feature.process(TENANT, "d1")
    assert "80287 münchen" in _node_values(kg, EntityType.GPE)

    # Re-extraction (post-#528): the doc now yields a bare city + a postal-code node instead.
    entities.delete_for_document(TENANT, "d1")
    entities.add_entities(
        [
            _mention("d1", "münchen", EntityType.GPE),
            _mention("d1", "80287", EntityType.POSTAL_CODE),
        ]
    )
    feature.process(TENANT, "d1")

    gpe = _node_values(kg, EntityType.GPE)
    assert "80287 münchen" not in gpe, "the orphaned fused node must be pruned on reprocess"
    assert "münchen" in gpe
    assert "80287" in _node_values(kg, EntityType.POSTAL_CODE)


def test_reprocess_keeps_node_still_mentioned_by_another_doc() -> None:
    entities = InMemoryEntityRepository()
    kg = InMemoryKnowledgeGraphRepository()
    feature = EntityGraphFeature(entities, kg)

    # Two docs both mention "berlin"; only d1 also has the soon-to-be-orphaned fused node.
    entities.add_entities(
        [
            _mention("d1", "berlin", EntityType.GPE),
            _mention("d1", "10115 berlin", EntityType.GPE),
        ]
    )
    entities.add_entities([_mention("d2", "berlin", EntityType.GPE)])
    feature.process(TENANT, "d1")
    feature.process(TENANT, "d2")

    # Re-extract only d1: it drops the fused node but keeps "berlin".
    entities.delete_for_document(TENANT, "d1")
    entities.add_entities([_mention("d1", "berlin", EntityType.GPE)])
    feature.process(TENANT, "d1")

    gpe = _node_values(kg, EntityType.GPE)
    assert "10115 berlin" not in gpe  # orphaned -> pruned
    assert "berlin" in gpe  # still mentioned by d1 and d2 -> kept
    assert canonical_entity_id(TENANT, "GPE", "berlin") in {e.id for e in kg.list_entities(TENANT)}
