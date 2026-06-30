"""Relation-adapter tests: a fake GLiNER-Relex model (no `gliner` runtime needed).

Test text is deliberately free of rule-relation trigger words (works for / located in / ...) so the
vendored RuleRelationExtractor stays silent and the assertions isolate the model-mapping logic.
"""

from __future__ import annotations

from typing import Any

from doktok_provider_gliner import GlinerRelexRelationExtractor
from doktok_provider_gliner.relation_adapter import _GlinerRelexPipeline

PERSON = ("Stefan Vogel", "PERSON")
ORG = ("Siemens AG", "ORG")
TEXT = "Record concerning Stefan Vogel and Siemens AG and Munich."


class _FakeRelexModel:
    """Stands in for the relex `UniEncoderSpanRelexGLiNER`: native one-pass `inference`."""

    def __init__(self, rels: list[dict[str, Any]]):
        # rels are head/tail-shaped dicts: {"head": {"text": ..}, "tail": {"text": ..}, "relation",
        # "score"} - the shape the real model returns.
        self._rels = rels

    def inference(
        self,
        texts: list[str],
        labels: Any = None,
        relations: Any = None,
        threshold: float = 0.3,
        relation_threshold: float = 0.5,
        return_relations: bool = True,
        flat_ner: bool = True,
        **_kwargs: Any,
    ) -> tuple[list[list[dict[str, Any]]], list[list[dict[str, Any]]]]:
        return ([[] for _ in texts], [self._rels for _ in texts])


class _FakePipeline:
    """Stands in for the relation pipeline; returns one batch of {source,relation,target} dicts."""

    def __init__(self, relations: list[dict[str, Any]]):
        self._relations = relations

    def __call__(
        self,
        texts: list[str],
        relations: Any = None,
        entities: Any = None,
        ner_threshold: float = 0.3,
        rel_threshold: float = 0.5,
        **_kwargs: Any,
    ) -> list[list[dict[str, Any]]]:
        return [self._relations]


def _rel(source: str, relation: str, target: str, score: float = 0.9) -> dict[str, Any]:
    return {"source": source, "relation": relation, "target": target, "score": score}


def _extractor(relations: list[dict[str, Any]]) -> GlinerRelexRelationExtractor:
    return GlinerRelexRelationExtractor(pipeline=_FakePipeline(relations))


def test_maps_natural_predicate_to_closed_vocabulary() -> None:
    out = _extractor([_rel("Stefan Vogel", "employed by", "Siemens AG")]).extract(
        TEXT, [PERSON, ORG]
    )
    assert len(out) == 1
    r = out[0]
    assert (r.subject, r.predicate, r.object) == ("Stefan Vogel", "EMPLOYED_BY", "Siemens AG")
    assert (r.subject_type, r.object_type) == ("PERSON", "ORG")


def test_reversed_direction_is_corrected() -> None:
    # Model emits ORG -> PERSON for a (PERSON, ORG) predicate; the adapter swaps endpoints.
    out = _extractor([_rel("Siemens AG", "works for", "Stefan Vogel")]).extract(TEXT, [PERSON, ORG])
    assert len(out) == 1
    r = out[0]
    assert (r.subject, r.predicate, r.object) == ("Stefan Vogel", "EMPLOYED_BY", "Siemens AG")
    assert (r.subject_type, r.object_type) == ("PERSON", "ORG")


def test_ungrounded_endpoint_is_dropped() -> None:
    out = _extractor([_rel("Stefan Vogel", "employed by", "Microsoft")]).extract(
        TEXT, [PERSON, ORG]
    )
    assert out == []


def test_unmapped_predicate_is_dropped() -> None:
    out = _extractor([_rel("Stefan Vogel", "acquired", "Siemens AG")]).extract(TEXT, [PERSON, ORG])
    assert out == []


def test_type_pair_not_allowed_is_dropped() -> None:
    # EMPLOYED_BY only allows (PERSON, ORG); an ORG-ORG pair must not produce a triple.
    out = _extractor([_rel("Siemens AG", "employed by", "Allianz")]).extract(
        TEXT, [ORG, ("Allianz", "ORG")]
    )
    assert out == []


def test_empty_entity_list_returns_empty() -> None:
    assert _extractor([_rel("Stefan Vogel", "employed by", "Siemens AG")]).extract(TEXT, []) == []


def _head_tail_rel(source: str, relation: str, target: str, score: float = 0.9) -> dict[str, Any]:
    return {
        "head": {"text": source},
        "tail": {"text": target},
        "relation": relation,
        "score": score,
    }


def test_relex_pipeline_normalizes_head_tail_output() -> None:
    # The native inference returns head/tail dicts; the pipeline flattens to source/relation/target.
    model = _FakeRelexModel([_head_tail_rel("Stefan Vogel", "employed by", "Siemens AG")])
    batch = _GlinerRelexPipeline(model)(
        ["some doc text"], relations=["employed by"], entities=["person", "organization"]
    )
    assert batch == [
        [
            {
                "source": "Stefan Vogel",
                "relation": "employed by",
                "target": "Siemens AG",
                "score": 0.9,
            }
        ]
    ]


def test_end_to_end_relex_model_to_extracted_relation() -> None:
    # Full path: fake relex model -> pipeline -> KAG flow -> grounded ExtractedRelation.
    model = _FakeRelexModel([_head_tail_rel("Stefan Vogel", "employed by", "Siemens AG")])
    extractor = GlinerRelexRelationExtractor(pipeline=_GlinerRelexPipeline(model))
    out = extractor.extract(TEXT, [PERSON, ORG])
    assert len(out) == 1
    assert (out[0].subject, out[0].predicate, out[0].object) == (
        "Stefan Vogel",
        "EMPLOYED_BY",
        "Siemens AG",
    )
