from doktok_core.documents.artifacts import extension_for


def test_extension_from_detected_mime() -> None:
    assert extension_for("application/pdf", "anything") == ".pdf"
    assert extension_for("text/plain", "weird.name") == ".txt"
    assert extension_for("text/markdown", "notes") == ".md"
    assert extension_for("image/png", "x") == ".png"


def test_extension_falls_back_to_filename_when_mime_unknown() -> None:
    assert extension_for("application/x-unknown-xyz", "report.PDF") == ".PDF"
    assert extension_for(None, "data.csv") == ".csv"
    assert extension_for(None, "noext") == ""
