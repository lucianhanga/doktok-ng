"""Graph export helpers.

The writer classes do not require Neo4j/Memgraph at import time. Use the Cypher
statements/params with your existing DB driver, or export JSONL for batch loads.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from .schemas import KGTriple, slugify


class JsonlGraphWriter:
    """Write graph triples as JSON Lines."""

    def write(self, triples: Sequence[KGTriple], path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for triple in triples:
                f.write(json.dumps(triple.to_dict(), ensure_ascii=False) + "\n")
        return path


class CypherGraphWriter:
    """Generate Neo4j/Memgraph-friendly Cypher MERGE queries.

    This returns statements and parameter dictionaries. It does not execute them,
    keeping this package DB-driver agnostic.
    """

    def build_statements(self, triples: Sequence[KGTriple]) -> list[tuple[str, dict]]:
        statements: list[tuple[str, dict]] = []
        for _idx, triple in enumerate(triples):
            params = triple.to_cypher_params()
            params["rel_type"] = self._safe_rel_type(triple.predicate)
            query = f"""
MERGE (s:Entity {{id: $subject_id}})
SET s.name = $subject_name, s.label = $subject_label
MERGE (o:Entity {{id: $object_id}})
SET o.name = $object_name, o.label = $object_label
MERGE (s)-[r:`{params["rel_type"]}`]->(o)
SET r.predicate = $predicate,
    r.confidence = $confidence,
    r.evidence_text = $evidence_text,
    r.source_doc_id = $source_doc_id,
    r.source_chunk_id = $source_chunk_id,
    r.qualifiers = $qualifiers,
    r.provenance = $provenance
""".strip()
            statements.append((query, params))
        return statements

    @staticmethod
    def _safe_rel_type(predicate: str) -> str:
        value = slugify(predicate).upper()
        if not value:
            return "RELATED_TO"
        return value


class NetworkXGraphWriter:
    """Optional NetworkX export."""

    def to_digraph(self, triples: Sequence[KGTriple]):
        try:
            import networkx as nx  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ImportError("Install networkx with: pip install kg-refiners[graph]") from exc
        graph = nx.MultiDiGraph()
        for triple in triples:
            graph.add_node(triple.subject_id, name=triple.subject_name, label=triple.subject_label)
            graph.add_node(triple.object_id, name=triple.object_name, label=triple.object_label)
            graph.add_edge(
                triple.subject_id,
                triple.object_id,
                key=triple.predicate,
                predicate=triple.predicate,
                confidence=triple.confidence,
                evidence_text=triple.evidence_text,
                source_doc_id=triple.source_doc_id,
                source_chunk_id=triple.source_chunk_id,
                qualifiers=triple.qualifiers,
                provenance=triple.provenance,
            )
        return graph


__all__ = ["JsonlGraphWriter", "CypherGraphWriter", "NetworkXGraphWriter"]
