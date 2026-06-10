from doktok_core.extraction.judge import choose_text
from doktok_core.extraction.quality import text_quality


def test_clean_text_scores_higher_than_garbage() -> None:
    clean = text_quality("This is a perfectly ordinary sentence of readable words.")
    garbage = text_quality("q3 !! @@ ## $$ %% z9 ^^ &&")
    assert clean > 0.6
    assert garbage < 0.4
    assert clean > garbage


def test_blank_is_zero() -> None:
    assert text_quality("   \n ") == 0.0


class _Chat:
    def __init__(self, reply: str) -> None:
        self._reply = reply

    def complete(self, prompt: str) -> str:  # noqa: ARG002
        return self._reply


def test_choose_text_uses_llm_verdict() -> None:
    assert choose_text("embedded", "ocr", chat_model=_Chat("A")) == ("embedded", False)
    assert choose_text("embedded", "ocr", chat_model=_Chat("B")) == ("ocr", True)


def test_choose_text_falls_back_to_heuristic_on_bad_verdict() -> None:
    # Unparseable verdict -> heuristic: cleaner text wins.
    chosen, used_ocr = choose_text("q3 !! @@", "clean readable words", chat_model=_Chat("???"))
    assert chosen == "clean readable words"
    assert used_ocr is True


def test_choose_text_heuristic_without_llm_keeps_better_embedded() -> None:
    chosen, used_ocr = choose_text("clean readable words", "q3 !! @@", chat_model=None)
    assert chosen == "clean readable words"
    assert used_ocr is False
