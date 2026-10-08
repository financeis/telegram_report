"""Extraction prompt.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_prompts.py
(the extraction tests; the diff prompt tests moved with the diff prompt to compare).
"""
import json

from research_desk.features.analysis.prompts import render_extraction_messages


def test_extraction_includes_normalization_rules():
    msgs = render_extraction_messages(
        report_metadata={'publisher': '삼성증권', 'stock_codes': ['005930'],
                         'published_at': '2026-05-05', 'title': 'X'},
        pages_text='--- Page 1 ---\n본문 ...',
    )
    sys = ''.join(m['content'] for m in msgs if m['role'] == 'system')
    user = ''.join(m['content'] for m in msgs if m['role'] == 'user')
    # 핵심 룰들이 prompt에 들어가 있는지 sanity check
    assert 'target_price' in sys.lower() or '목표주가' in sys
    assert 'BUY' in sys or '매수' in sys  # recommendation mapping
    assert 'Trading Buy' in sys             # 한국 sell-side 표현 포함
    assert 'Outperform' in sys
    assert 'metadata' in sys.lower()         # trust metadata 한 줄
    assert '005930' in user                  # metadata가 user msg에
    assert '--- Page 1 ---' in user          # page-numbered text 포함


def test_extraction_traps_section():
    msgs = render_extraction_messages(
        report_metadata={}, pages_text='--- Page 1 ---\n')
    sys = ''.join(m['content'] for m in msgs if m['role'] == 'system')
    assert 'current price' in sys.lower() or '현재가' in sys
    assert 'market cap' in sys.lower() or '시가총액' in sys


def test_extraction_messages_shape():
    # system first, then the user payload: metadata JSON (Korean kept) + page text.
    metadata = {'publisher': '삼성증권', 'stock_codes': ['005930']}
    pages = '--- Page 1 ---\n본문\n'
    system, user = render_extraction_messages(metadata, pages)
    assert system['role'] == 'system' and user['role'] == 'user'
    assert user['content'] == (
        f"<report_metadata>\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n</report_metadata>\n\n"
        f"<report_pages>\n{pages}\n</report_pages>"
    )
    assert '<report_pages>' not in system['content']
    # the system text is the same for every report (cacheable)
    assert render_extraction_messages({}, '')[0] == system
