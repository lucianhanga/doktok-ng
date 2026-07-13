"""PERSON name-part parsing (#531): given/middle/family from a surface form, safe on non-US names."""  # noqa: E501

from __future__ import annotations

from doktok_core.knowledge_graph.name_parts import parse_person_name


def _family(surface: str) -> str:
    parts = parse_person_name(surface)
    assert parts is not None
    return str(parts["family_name"])


def test_simple_three_part_name() -> None:
    parts = parse_person_name("Lucian Cosmin Hanga")
    assert parts is not None
    assert parts["given_name"] == "Lucian"
    assert parts["middle_names"] == ["Cosmin"]
    assert parts["family_name"] == "Hanga"
    assert parts["name_parse_confidence"] >= 0.5


def test_shared_surname_example_from_532() -> None:
    # The exact pair #532 must be able to group by surname "Hanga" - probablepeople tagged the
    # first as a Corporation and dropped the surname; nameparser gets both.
    a = parse_person_name("Daniel Dennis Hanga")
    b = parse_person_name("Lucian Cosmin Hanga")
    assert a is not None and b is not None
    assert a["family_name"] == b["family_name"] == "Hanga"


def test_two_part_name() -> None:
    parts = parse_person_name("Angela Merkel")
    assert parts is not None
    assert parts["given_name"] == "Angela"
    assert parts["family_name"] == "Merkel"
    assert "middle_names" not in parts


def test_german_particles_stay_with_surname() -> None:
    assert _family("Ludwig von Beethoven") == "von Beethoven"
    assert _family("Ursula von der Leyen") == "von der Leyen"
    assert _family("Johann Wolfgang von Goethe") == "von Goethe"


def test_spanish_particle_surname() -> None:
    assert _family("Maria de la Cruz") == "de la Cruz"


def test_title_is_stripped_not_a_surname() -> None:
    parts = parse_person_name("Dr. Angela Merkel")
    assert parts is not None
    assert parts["family_name"] == "Merkel"
    assert parts["given_name"] == "Angela"


def test_hyphenated_given_name() -> None:
    parts = parse_person_name("Hans-Peter Müller")
    assert parts is not None
    assert parts["given_name"] == "Hans-Peter"
    assert parts["family_name"] == "Müller"


def test_comma_inverted_form() -> None:
    parts = parse_person_name("Cotirlea, Viviana")
    assert parts is not None
    assert parts["given_name"] == "Viviana"
    assert parts["family_name"] == "Cotirlea"


def test_single_token_yields_no_surname() -> None:
    assert parse_person_name("München") is None
    assert parse_person_name("Angela") is None


def test_digits_are_rejected() -> None:
    assert parse_person_name("Room 5B") is None
    assert parse_person_name("Agent 007") is None


def test_empty_and_whitespace() -> None:
    assert parse_person_name("") is None
    assert parse_person_name("   ") is None
