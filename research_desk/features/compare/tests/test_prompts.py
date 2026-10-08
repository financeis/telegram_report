"""The comparison prompt: same-publisher revision or two desks' views.

Ported from langgraph_tagger/analytics/llm_summary/tests/test_prompts.py, the diff tests:
test_diff_same_publisher_framing, test_diff_cross_publisher_framing, test_diff_defensive_null_guard.

New: system then user; the user message carries the pair context and both summaries as JSON (Korean
kept as is) and the two empty excerpt blocks; only the different-publisher framing names the
publishers.
"""
import json

from research_desk.features.compare.prompts import render_diff_messages


def test_diff_same_publisher_framing():
    prev_summary = {'target_price_new': 70000, 'recommendation': '매수',
                    'one_line_summary': '이전 view', 'positive_points': [],
                    'risk_points': [], 'target_price_dir': '신규',
                    'recommendation_dir': '신규',
                    'target_price_raw': '7만원', 'recommendation_raw': 'Buy'}
    curr_summary = {'target_price_new': 85000, 'recommendation': '매수',
                    'one_line_summary': '현재 view', 'positive_points': [],
                    'risk_points': [], 'target_price_dir': '상향',
                    'recommendation_dir': '유지',
                    'target_price_raw': '8.5만원', 'recommendation_raw': 'Buy'}
    msgs = render_diff_messages(
        prev_summary, curr_summary, prev_match_type='same_publisher',
        prev_report_id=1, prev_publisher='삼성증권', curr_publisher='삼성증권',
    )
    sys = ''.join(m['content'] for m in msgs if m['role'] == 'system')
    assert 'same' in sys.lower() or '동일' in sys or '시계열' in sys


def test_diff_cross_publisher_framing():
    prev_summary = {'target_price_new': 70000, 'recommendation': '매수',
                    'one_line_summary': 'A view', 'positive_points': [],
                    'risk_points': [], 'target_price_dir': '신규',
                    'recommendation_dir': '신규',
                    'target_price_raw': '7만원', 'recommendation_raw': 'Buy'}
    curr_summary = {'target_price_new': 85000, 'recommendation': '매수',
                    'one_line_summary': 'B view', 'positive_points': [],
                    'risk_points': [], 'target_price_dir': '신규',
                    'recommendation_dir': '신규',
                    'target_price_raw': '8.5만원', 'recommendation_raw': 'Buy'}
    msgs = render_diff_messages(
        prev_summary, curr_summary, prev_match_type='cross_publisher',
        prev_report_id=2, prev_publisher='삼성증권', curr_publisher='미래에셋',
    )
    sys = ''.join(m['content'] for m in msgs if m['role'] == 'system')
    # cross-publisher 분기: revision 아니라 두 애널리스트 비교 framing
    assert 'revision' in sys.lower() or '비교' in sys or '다른' in sys
    assert '삼성증권' in sys or '미래에셋' in sys


def test_diff_defensive_null_guard():
    """prompt에 'no previous → null' 방어줄 유지."""
    msgs = render_diff_messages(
        prev_summary={}, curr_summary={},
        prev_match_type='same_publisher',
        prev_report_id=1, prev_publisher='', curr_publisher='',
    )
    sys = ''.join(m['content'] for m in msgs if m['role'] == 'system')
    assert 'null' in sys.lower() or 'no previous' in sys.lower()


# ── new ──────────────────────────────────────────────────────────────────────

def _block(text: str, tag: str) -> str:
    start = text.index(f'<{tag}>\n') + len(tag) + 3
    return text[start:text.index(f'\n</{tag}>')]


def test_messages_are_system_then_user():
    msgs = render_diff_messages({}, {}, 'same_publisher', 1, 'KB', 'KB')
    assert [m['role'] for m in msgs] == ['system', 'user']
    assert 'diff_narrative=null' in msgs[0]['content']


def test_the_user_message_carries_the_pair_and_both_summaries():
    previous = {'target_price_new': 70000, 'one_line_summary': '이전 view',
                'financial_details': {'metrics': []}}
    current = {'target_price_new': 85000, 'one_line_summary': '현재 view'}
    system, user = render_diff_messages(previous, current, 'cross_publisher', 2, '삼성증권', '미래에셋')
    text = user['content']
    assert json.loads(_block(text, 'comparison_context')) == {
        'prev_report_id': 2, 'prev_match_type': 'cross_publisher',
        'prev_publisher': '삼성증권', 'curr_publisher': '미래에셋'}
    assert json.loads(_block(text, 'previous_summary')) == previous
    assert json.loads(_block(text, 'current_summary')) == current
    assert '이전 view' in text and '\\u' not in text   # Korean as is, not escaped
    assert text.endswith('<optional_previous_excerpt></optional_previous_excerpt>\n\n'
                         '<optional_current_excerpt></optional_current_excerpt>')
    assert '<previous_summary>' not in system['content']


def test_only_the_different_publisher_framing_names_the_publishers():
    same = render_diff_messages({}, {}, 'same_publisher', 1, '삼성증권', '삼성증권')[0]['content']
    cross = render_diff_messages({}, {}, 'cross_publisher', 1, '삼성증권', '미래에셋')[0]['content']
    assert '**same publisher**' in same and '**different publishers**' not in same
    assert '삼성증권' not in same
    assert '**different publishers**' in cross and '**same publisher**' not in cross
    assert 'previous: 삼성증권, current: 미래에셋' in cross
    assert 'DO NOT use language implying the same analyst revised their view.' in cross
