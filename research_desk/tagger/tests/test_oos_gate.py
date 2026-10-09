"""Tests for oos_gate (3-way routing function — does NOT mutate state)."""
import pytest

from research_desk.tagger.llm_schemas import LLMExtraction, OOSSignals
from research_desk.tagger.nodes.oos_gate import oos_gate
from research_desk.tagger.tests.conftest import make_llm_extraction


def _ext(**oos_kwargs) -> LLMExtraction:
    sig = OOSSignals(
        foreign_primary_coverage=oos_kwargs.get("foreign", False),
        etf_or_fund=oos_kwargs.get("etf", False),
        digital_asset=oos_kwargs.get("digital", False),
        private_company_likely=oos_kwargs.get("private", False),
    )
    return make_llm_extraction(
        oos_signals=sig,
        report_type=oos_kwargs.get("report_type", "단일종목"),
        stock_codes_raw=oos_kwargs.get("stock_codes_raw", []),
    )


def test_pdf_unreadable_routes_to_status_unreadable(krx):
    state = {"pdf_unreadable": True, "llm_raw": None}
    assert oos_gate(state, krx=krx) == "status_unreadable"
    # state must NOT be mutated
    assert "oos_reason" not in state


def test_llm_refusal_routes_to_status_unreadable(krx):
    state = {"pdf_unreadable": False, "llm_raw": None, "llm_refusal": "policy"}
    assert oos_gate(state, krx=krx) == "status_unreadable"
    assert "oos_reason" not in state


def test_foreign_routes_to_mark_oos(krx):
    state = {"llm_raw": _ext(foreign=True)}
    assert oos_gate(state, krx=krx) == "mark_oos_reason"
    # routing-only — does NOT set oos_reason here
    assert "oos_reason" not in state


def test_fund_routes_to_mark_oos(krx):
    state = {"llm_raw": _ext(etf=True)}
    assert oos_gate(state, krx=krx) == "mark_oos_reason"


def test_digital_routes_to_mark_oos(krx):
    state = {"llm_raw": _ext(digital=True)}
    assert oos_gate(state, krx=krx) == "mark_oos_reason"


def test_private_with_no_krx_match_routes_to_mark_oos(krx):
    state = {"llm_raw": _ext(private=True, stock_codes_raw=["999999"])}
    assert oos_gate(state, krx=krx) == "mark_oos_reason"


def test_private_with_krx_match_stays_in_scope(krx):
    # spec §6.5 rule 4: KRX-matched code → in-scope even if private signal is set
    state = {"llm_raw": _ext(private=True, stock_codes_raw=["005930"])}
    assert oos_gate(state, krx=krx) == "resolve_krx"


def test_ir_material_routes_to_mark_oos_reason(krx):
    # v2: IR자료 = 자동 OOS ir_self (mark_oos_reason에서 reason 결정)
    raw = make_llm_extraction(
        report_type="IR자료",
        publisher_canon="해당기업",
        publisher_type="other",
        stock_codes_raw=[],
        company_names_raw=["비상장기업명"],
    )
    state = {"llm_raw": raw, "pdf_unreadable": False, "llm_refusal": None}
    assert oos_gate(state, krx=krx) == "mark_oos_reason"


def test_ipo_no_longer_in_enum_so_no_special_branch(krx):
    """v2: IPO는 enum에서 제거됨 (단일종목으로 통합). private_company_likely 분기에 IPO 예외 없음."""
    raw = make_llm_extraction(
        report_type="단일종목",
        stock_codes_raw=[],  # KRX unmatched
        company_names_raw=["미상장IPO후보"],
        oos_signals=OOSSignals(
            foreign_primary_coverage=False, etf_or_fund=False,
            digital_asset=False, private_company_likely=True,
        ),
    )
    state = {"llm_raw": raw, "pdf_unreadable": False, "llm_refusal": None}
    # private + KRX unmatched + 단일종목 → OOS private (IPO 예외 없음)
    assert oos_gate(state, krx=krx) == "mark_oos_reason"


def test_in_scope_default_routes_to_resolve_krx(krx):
    state = {"llm_raw": _ext()}
    assert oos_gate(state, krx=krx) == "resolve_krx"


def test_routing_function_does_not_mutate_state_in_any_branch(krx):
    """Defense-in-depth: snapshot before/after to confirm no mutation."""
    import copy
    state = {"llm_raw": _ext(foreign=True)}
    snapshot = copy.deepcopy(state)
    oos_gate(state, krx=krx)
    assert state == snapshot
