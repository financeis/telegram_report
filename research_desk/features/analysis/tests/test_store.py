"""report_summaries reads and writes (Supabase REST, mocked).

Ported from langgraph_tagger/analytics/llm_summary/tests/test_summary_store.py.
"""
from unittest.mock import MagicMock

import pytest

from research_desk.features.analysis import store


class FakeQuery:
    """Chainable supabase-py REST query mock."""
    def __init__(self, table, response_data):
        self.table = table
        self._response_data = response_data
        self.calls = []
    def select(self, cols):
        self.calls.append(('select', cols)); return self
    def in_(self, col, vals):
        self.calls.append(('in_', col, list(vals))); return self
    def eq(self, col, val):
        self.calls.append(('eq', col, val)); return self
    def update(self, payload):
        self.calls.append(('update', payload)); return self
    def upsert(self, payload, on_conflict=None):
        self.calls.append(('upsert', payload, on_conflict)); return self
    def execute(self):
        self.calls.append(('execute',))
        resp = MagicMock()
        resp.data = self._response_data
        return resp


class FakeSupabase:
    def __init__(self, response_data=None):
        self.last_query = None
        self._response_data = response_data or []
    def table(self, name):
        self.last_query = FakeQuery(name, self._response_data)
        return self.last_query


def test_fetch_summaries_filters_active_version():
    rows = [{'report_id': 1, 'summary_version': 'llm-summary@1.0'}]
    sb = FakeSupabase(rows)
    out = store.fetch_summaries(sb, [1, 2, 3], active_version='llm-summary@1.0')
    assert isinstance(out, dict)
    assert 1 in out
    assert sb.last_query.table == 'report_summaries'
    calls = sb.last_query.calls
    assert ('select', '*') in calls
    assert ('in_', 'report_id', [1, 2, 3]) in calls
    assert ('eq', 'summary_version', 'llm-summary@1.0') in calls


def test_fetch_summaries_without_ids_does_not_query():
    sb = FakeSupabase([{'report_id': 1}])
    assert store.fetch_summaries(sb, [], active_version='llm-summary@1.0') == {}
    assert sb.last_query is None


def test_upsert_summary_includes_null_diff_fields():
    """diff reset 의무: payload엔 항상 prev_*, diff_narrative NULL 포함."""
    sb = FakeSupabase()
    store.upsert_summary(sb, {
        'report_id': 1,
        'target_price_new': 85000, 'target_price_old': 70000,
        'target_price_dir': '상향', 'recommendation': '매수',
        'recommendation_dir': '유지',
        'one_line_summary': 'X', 'positive_points': [], 'risk_points': [],
        'target_price_raw': '8.5만원', 'recommendation_raw': 'Buy',
        'source_pages': [1], 'extraction_confidence': 'high',
        'input_truncated': False, 'input_pages_used': 5, 'input_total_pages': 5,
        'summary_version': 'llm-summary@1.0', 'llm_model': 'gpt-5.4-mini',
        'llm_tokens_input': 1000, 'llm_tokens_output': 200,
        'prev_report_id': None,
        'prev_match_type': None,
        'diff_narrative': None,
    })
    assert sb.last_query.table == 'report_summaries'
    upsert_call = [c for c in sb.last_query.calls if c[0] == 'upsert'][0]
    payload = upsert_call[1]
    assert payload['prev_report_id'] is None
    assert payload['prev_match_type'] is None
    assert payload['diff_narrative'] is None
    assert upsert_call[2] == 'report_id'  # on_conflict
    assert sb.last_query.calls[-1] == ('execute',)


@pytest.mark.parametrize('field,value', [
    ('prev_report_id', 7), ('prev_match_type', 'same_publisher'), ('diff_narrative', '이전 대비'),
])
def test_upsert_summary_refuses_comparison_values(field, value):
    sb = FakeSupabase()
    with pytest.raises(AssertionError):
        store.upsert_summary(sb, {'report_id': 1, 'prev_report_id': None, 'prev_match_type': None,
                                  'diff_narrative': None, field: value})
    assert sb.last_query is None  # nothing written


def test_update_diff_sets_match_and_narrative():
    sb = FakeSupabase()
    store.update_diff(
        sb, report_id=42, prev_report_id=10,
        match_type='same_publisher', narrative='이전 대비 ...',
    )
    update_call = [c for c in sb.last_query.calls if c[0] == 'update'][0]
    payload = update_call[1]
    assert payload['prev_report_id'] == 10
    assert payload['prev_match_type'] == 'same_publisher'
    assert payload['diff_narrative'].startswith('이전')
    assert ('eq', 'report_id', 42) in sb.last_query.calls


def test_update_diff_marks_none_when_no_prev():
    sb = FakeSupabase()
    store.update_diff(
        sb, report_id=42, prev_report_id=None,
        match_type='none', narrative=None,
    )
    update_call = [c for c in sb.last_query.calls if c[0] == 'update'][0]
    payload = update_call[1]
    assert payload['prev_report_id'] is None
    assert payload['prev_match_type'] == 'none'
    assert payload['diff_narrative'] is None


def test_store_comparison_details():
    sb = FakeSupabase()
    store.update_diff(sb, report_id=3, prev_report_id=2,
                      match_type='same_publisher', narrative='전망 상향',
                      comparison_details={'metrics': [{'previous': 100, 'current': 120}]})
    payload = next(c[1] for c in sb.last_query.calls if c[0] == 'update')
    assert payload['comparison_details']['metrics'][0]['current'] == 120
    # only the four comparison fields are written
    assert set(payload) == {'prev_report_id', 'prev_match_type', 'diff_narrative',
                            'comparison_details'}
