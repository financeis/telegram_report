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
