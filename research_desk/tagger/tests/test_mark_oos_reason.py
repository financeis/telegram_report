"""Tests for mark_oos_reason node."""
from research_desk.tagger.llm_schemas import OOSSignals
from research_desk.tagger.nodes.mark_oos_reason import mark_oos_reason
from research_desk.tagger.tests.conftest import make_llm_extraction


def _state(**flags):
    sig = OOSSignals(
        foreign_primary_coverage=flags.get("foreign", False),
        etf_or_fund=flags.get("etf", False),
        digital_asset=flags.get("digital", False),
        private_company_likely=flags.get("private", False),
    )
    return {"llm_raw": make_llm_extraction(oos_signals=sig)}


def test_foreign_first():
    out = mark_oos_reason(_state(foreign=True))
    assert out == {"is_oos": True, "oos_reason": "foreign"}


def test_fund():
    out = mark_oos_reason(_state(etf=True))
    assert out == {"is_oos": True, "oos_reason": "fund"}


def test_digital():
    out = mark_oos_reason(_state(digital=True))
    assert out == {"is_oos": True, "oos_reason": "digital"}


def test_private_only():
    # When only private_company_likely is true (and oos_gate already excluded
    # the KRX-matched exception), result is 'private'.
    out = mark_oos_reason(_state(private=True))
    assert out == {"is_oos": True, "oos_reason": "private"}


def test_priority_foreign_beats_others():
    # If multiple flags are true, foreign takes priority (matches oos_gate routing order)
    out = mark_oos_reason(_state(foreign=True, etf=True, digital=True, private=True))
    assert out["oos_reason"] == "foreign"


def test_ir_material_yields_ir_self_reason():
    # v2: IR자료 → auto OOS ir_self (priority over signals)
    raw = make_llm_extraction(
        report_type="IR자료",
        publisher_canon="해당기업",
        publisher_type="other",
    )
    out = mark_oos_reason({"llm_raw": raw})
    assert out == {"is_oos": True, "oos_reason": "ir_self"}
