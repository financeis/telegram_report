import pytest
from research_desk.tagger.nodes.status_oos import status_oos


@pytest.mark.parametrize("reason,expected_conf", [
    ("foreign", "high"),
    ("fund", "high"),
    ("digital", "high"),
    ("private", "medium"),
])
def test_status_oos_sets_status_and_confidence(reason, expected_conf):
    state = {"oos_reason": reason}
    out = status_oos(state)
    assert out["is_oos"] is True
    assert out["tagging_status"] == "auto"
    assert out["tagging_confidence"] == expected_conf
    assert out["tagging_notes"] is None


def test_ir_self_is_high_confidence():
    from research_desk.tagger.nodes.status_oos import status_oos

    out = status_oos({"oos_reason": "ir_self"})
    assert out == {
        "is_oos": True,
        "tagging_status": "auto",
        "tagging_confidence": "high",
        "tagging_notes": None,
    }


def test_private_remains_medium():
    from research_desk.tagger.nodes.status_oos import status_oos

    out = status_oos({"oos_reason": "private"})
    assert out["tagging_confidence"] == "medium"


# ── Rules applied last: page_image / publisher_suspect (spec §3.5, §3.2) ─────

@pytest.mark.parametrize("reason", ["foreign", "fund", "digital", "ir_self", "private"])
def test_a_picture_row_is_at_most_medium_with_the_page_image_note(reason):
    out = status_oos({"oos_reason": reason, "page_image": True})
    assert out == {
        "is_oos": True,
        "tagging_status": "auto",
        "tagging_confidence": "medium",
        "tagging_notes": "page_image",
    }


@pytest.mark.parametrize("suspect", ["unknown", "filename_mismatch"])
@pytest.mark.parametrize("reason", ["foreign", "fund", "digital", "ir_self", "private"])
def test_a_suspect_row_is_at_most_medium_with_the_suspect_note(reason, suspect):
    out = status_oos({"oos_reason": reason, "publisher_suspect": suspect})
    assert out == {
        "is_oos": True,
        "tagging_status": "auto",
        "tagging_confidence": "medium",
        "tagging_notes": f"publisher_suspect:{suspect}",
    }


def test_picture_and_suspect_notes_in_order():
    out = status_oos({"oos_reason": "fund", "page_image": True, "publisher_suspect": "unknown"})
    assert out["tagging_confidence"] == "medium"
    assert out["tagging_notes"] == "page_image;publisher_suspect:unknown"


@pytest.mark.parametrize("reason,expected_conf", [
    ("foreign", "high"), ("fund", "high"), ("digital", "high"), ("ir_self", "high"),
    ("private", "medium"),
])
def test_unmarked_oos_rows_are_unchanged(reason, expected_conf):
    out = status_oos({"oos_reason": reason, "page_image": False, "publisher_suspect": None})
    assert out == {
        "is_oos": True,
        "tagging_status": "auto",
        "tagging_confidence": expected_conf,
        "tagging_notes": None,
    }
