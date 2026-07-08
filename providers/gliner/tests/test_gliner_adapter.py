"""Adapter tests: a fake GLiNER model (the `gliner` runtime is not installed for unit tests)."""

from __future__ import annotations

from doktok_contracts.media import ExtractedEntity
from doktok_contracts.schemas import EntityType
from doktok_provider_gliner import GlinerEntityNerExtractor, NuNerEntityNerExtractor


class _FakeModel:
    """Stands in for `gliner.GLiNER`: finds canned surfaces and returns span dicts per chunk."""

    def __init__(
        self, surfaces: dict[str, list[str]], scores: dict[str, float] | None = None
    ) -> None:
        # surfaces maps a model label -> list of surface strings to locate in each chunk;
        # scores maps a surface -> its confidence (default 0.95, i.e. above every threshold).
        self.surfaces = surfaces
        self.scores = scores or {}
        self.calls = 0

    def predict_entities(
        self, text: str, labels: list[str], threshold: float = 0.0
    ) -> list[dict[str, object]]:
        self.calls += 1
        out: list[dict[str, object]] = []
        for label in labels:
            for surface in self.surfaces.get(label, []):
                pos = text.find(surface)
                if pos < 0:
                    continue
                out.append(
                    {
                        "text": surface,
                        "label": label,
                        "start": pos,
                        "end": pos + len(surface),
                        "score": self.scores.get(surface, 0.95),
                    }
                )
        return out


def test_maps_labels_to_entity_types_and_offsets() -> None:
    model = _FakeModel(
        {
            "person": ["Stefan Vogel"],
            "organization": ["Deutsche Bank"],
            "location": ["Munich"],
        }
    )
    extractor = GlinerEntityNerExtractor(model=model)
    text = "Stefan Vogel banks with Deutsche Bank in Munich."
    out = extractor.extract(text)

    by_type = {e.entity_type: e for e in out}
    assert by_type[EntityType.PERSON].entity_text == "Stefan Vogel"
    assert by_type[EntityType.ORG].normalized_value == "Deutsche Bank"
    assert by_type[EntityType.GPE].entity_text == "Munich"
    # offsets are real document coordinates, not (0, 0) like the LLM adapters
    gpe = by_type[EntityType.GPE]
    assert text[gpe.start_offset : gpe.end_offset] == "Munich"
    assert all(isinstance(e, ExtractedEntity) for e in out)


def test_unrequested_labels_are_dropped() -> None:
    # The model returns a label the adapter never requested; it must not leak through.
    model = _FakeModel({"person": ["Ada"], "email": ["ada@x.com"]})
    out = GlinerEntityNerExtractor(model=model).extract("Ada is ada@x.com")
    assert {e.entity_type for e in out} == {EntityType.PERSON}


def test_windows_long_text_and_shifts_offsets() -> None:
    # A small window forces splitting; the entity in the second window must get a shifted offset.
    filler = "x " * 1200  # ~2400 chars, larger than the default 1500-char window
    text = f"Acme Corp opened. {filler} Then Globex Inc arrived."
    model = _FakeModel({"organization": ["Acme Corp", "Globex Inc"]})
    extractor = GlinerEntityNerExtractor(model=model, window_chars=1500)
    out = extractor.extract(text)

    assert model.calls >= 2  # the document was split into multiple windows
    names = {e.normalized_value for e in out}
    assert names == {"Acme Corp", "Globex Inc"}
    for e in out:
        assert text[e.start_offset : e.end_offset] == e.entity_text


def test_deduplicates_repeated_entities_across_windows() -> None:
    text = "Acme Corp. " * 400  # repeats well past one window
    model = _FakeModel({"organization": ["Acme Corp"]})
    out = GlinerEntityNerExtractor(model=model, window_chars=500).extract(text)
    assert [e.normalized_value for e in out] == ["Acme Corp"]  # collapsed to one


def test_nuner_adapter_shares_behaviour() -> None:
    model = _FakeModel({"person": ["Maria"], "organization": ["Akme GmbH"]})
    out = NuNerEntityNerExtractor(model=model).extract("Maria joined Akme GmbH.")
    assert {(e.entity_type, e.normalized_value) for e in out} == {
        (EntityType.PERSON, "Maria"),
        (EntityType.ORG, "Akme GmbH"),
    }


def test_job_title_labels_map_to_job_title_multilingually() -> None:
    """Both open-vocabulary job-title labels map to JOB_TITLE; the label prompt is language-
    agnostic, so German and English titles come back from the same request (#518 Phase 2)."""
    model = _FakeModel(
        {
            "job title": ["Geschäftsführerin", "Senior Software Engineer"],
            "professional role": ["Steuerberater"],
            "person": ["Maria Weber"],
        }
    )
    text = (
        "Maria Weber ist Geschäftsführerin. Der Steuerberater prüft alles. "
        "A Senior Software Engineer reviewed the contract."
    )
    out = GlinerEntityNerExtractor(model=model).extract(text)

    titles = {e.normalized_value for e in out if e.entity_type is EntityType.JOB_TITLE}
    assert titles == {"Geschäftsführerin", "Senior Software Engineer", "Steuerberater"}
    # PERSON extraction is unchanged by the additional labels.
    assert {e.normalized_value for e in out if e.entity_type is EntityType.PERSON} == {
        "Maria Weber"
    }
    for e in out:
        assert text[e.start_offset : e.end_offset] == e.entity_text


def test_job_title_confidence_gate_drops_low_confidence_spans() -> None:
    """JOB_TITLE uses a stricter acceptance threshold (0.70) than the named-entity default (0.50):
    a common-noun span the model half-believes is a title (0.60) must be dropped, while a person
    span at the same confidence passes. This is the precision gate against over-firing on generic
    nouns like 'Mitarbeiter'."""
    model = _FakeModel(
        {"job title": ["Mitarbeiter", "Rechtsanwältin"], "person": ["Stefan Vogel"]},
        scores={"Mitarbeiter": 0.60, "Rechtsanwältin": 0.88, "Stefan Vogel": 0.60},
    )
    text = "Der Mitarbeiter sprach mit Stefan Vogel und der Rechtsanwältin."
    out = GlinerEntityNerExtractor(model=model).extract(text)

    titles = {e.normalized_value for e in out if e.entity_type is EntityType.JOB_TITLE}
    assert titles == {"Rechtsanwältin"}  # 0.60 < 0.70 gate: 'Mitarbeiter' dropped
    persons = {e.normalized_value for e in out if e.entity_type is EntityType.PERSON}
    assert persons == {"Stefan Vogel"}  # same 0.60 passes the default 0.50 threshold


def test_max_chars_caps_input() -> None:
    # An entity beyond the max-chars budget is never seen by the model.
    head = "A " * 10
    tail = "Zeta Corp"
    text = head + ("y " * 7000) + tail  # tail sits past the 12k-char cap
    model = _FakeModel({"organization": ["Zeta Corp"]})
    out = GlinerEntityNerExtractor(model=model, max_chars=200).extract(text)
    assert out == []
