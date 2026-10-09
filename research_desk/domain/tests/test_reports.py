"""research_desk.domain.reports: vocabulary value sets, the in-scope rule, the out-of-scope and
pending-reset row shapes.

Value-set tests are ported from langgraph_tagger/tests/test_vocabulary.py.
The out-of-scope row shape tests pin what langgraph_tagger/nodes/write.py's OOS branch
and langgraph_tagger/review_viewer/actions.py::build_oos_payload produced (spec §9.2).
The pending-reset row shape tests pin what features/review/rules.build_pending_reset_payload
wrote before that shape moved here.
"""
from __future__ import annotations

import copy

import pytest
import yaml

from research_desk.domain import reports


# --- value sets (ported from test_vocabulary.py) -----------------------------------------

def test_six_report_types_in_order():
    assert reports.REPORT_TYPES == ("단일종목", "산업", "섹터", "IR자료", "전략·시황", "기타")


def test_oos_reasons_include_ir_self():
    assert "ir_self" in reports.OOS_REASONS
    assert reports.OOS_REASONS == ("foreign", "fund", "digital", "private", "ir_self")


def test_publisher_types_drop_company():
    assert "company" not in reports.PUBLISHER_TYPES
    assert reports.PUBLISHER_TYPES == ("broker", "data_provider", "ir_agency", "other")


def test_tagging_statuses_and_confidences():
    assert reports.TAGGING_STATUSES == ("pending", "processing", "auto", "review_needed", "verified")
    assert reports.TAGGING_CONFIDENCES == ("high", "medium", "low")


def test_publisher_lookup_and_topic_mapping_not_exported():
    # v2: the LLM outputs publisher_canon itself; topics were removed.
    assert not hasattr(reports, "lookup_publisher")
    assert not hasattr(reports, "map_topics")


def test_value_sets_are_read_from_vocabulary_yaml():
    data = yaml.safe_load(reports.VOCABULARY_PATH.read_text(encoding="utf-8"))
    assert reports.VOCABULARY_PATH.name == "vocabulary.yaml"
    assert reports.VOCABULARY_PATH.parent.name == "domain"
    assert reports.REPORT_TYPES == tuple(data["report_types"])
    assert reports.OOS_REASONS == tuple(data["oos_reasons"])
    assert reports.PUBLISHER_TYPES == tuple(data["publisher_types"])
    assert reports.TAGGING_STATUSES == tuple(data["tagging_statuses"])
    assert reports.TAGGING_CONFIDENCES == tuple(data["tagging_confidences"])
    # Reference notes for the tagger's precedence rules stay with the vocabulary.
    assert len(data["precedence_rules"]) == 6


def test_vocabulary_file_has_no_version_field():
    # spec §8: taxonomy_version is the stock-list version; changing these values needs a migration.
    data = yaml.safe_load(reports.VOCABULARY_PATH.read_text(encoding="utf-8"))
    assert "version" not in data
    assert "taxonomy_version" not in data


def test_load_vocabulary_reads_a_given_file(tmp_path):
    other = tmp_path / "vocab.yaml"
    other.write_text("report_types: [단일종목]\n", encoding="utf-8")
    assert reports.load_vocabulary(other) == {"report_types": ["단일종목"]}
    assert reports.load_vocabulary()["oos_reasons"] == list(reports.OOS_REASONS)


# --- in-scope rule ---------------------------------------------------------------------------

def test_in_scope_statuses():
    assert reports.IN_SCOPE_STATUSES == ("auto", "verified")
    assert set(reports.IN_SCOPE_STATUSES) <= set(reports.TAGGING_STATUSES)


@pytest.mark.parametrize("status", ["auto", "verified"])
def test_in_scope_when_status_is_final_and_reason_is_null(status):
    assert reports.is_in_scope({"tagging_status": status, "out_of_scope_reason": None}) is True


@pytest.mark.parametrize("status", ["pending", "processing", "review_needed", None, "AUTO", ""])
def test_not_in_scope_for_any_other_status(status):
    assert reports.is_in_scope({"tagging_status": status, "out_of_scope_reason": None}) is False


@pytest.mark.parametrize("reason", ["foreign", "fund", "digital", "private", "ir_self"])
@pytest.mark.parametrize("status", ["auto", "verified"])
def test_not_in_scope_when_an_oos_reason_is_set(status, reason):
    assert reports.is_in_scope({"tagging_status": status, "out_of_scope_reason": reason}) is False


def test_missing_reason_key_counts_as_null_and_missing_status_is_not_in_scope():
    assert reports.is_in_scope({"tagging_status": "auto"}) is True
    assert reports.is_in_scope({"out_of_scope_reason": None}) is False
    assert reports.is_in_scope({}) is False


# --- out-of-scope row shape --------------------------------------------------------------------

def make_row(**overrides):
    """A representative review_needed reports row (supabase-py response shape)."""
    base = {
        'id': 1234,
        'message_id': 124784,
        'chat_username': 'sunstudy1004',
        'file_path': '124784_some.pdf',
        'file_name': 'some.pdf',
        'file_size_bytes': 12345,
        'file_hash_sha256': 'a' * 64,
        'caption': 'Sample',
        'downloaded_at': '2026-05-07T08:04:14+00:00',
        'sent_at': '2026-05-07T08:04:14+00:00',
        'published_at': '2026-05-07',
        'report_type': '단일종목',
        'publisher': '삼성증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': '삼성전자 Q1',
        'stock_codes': ['005930'],
        'company_names': ['삼성전자'],
        'stock_codes_raw': ['005935'],
        'company_names_raw': ['삼성전자우'],
        'sectors_major': ['반도체'],
        'sectors_minor': ['메모리'],
        'products': ['DRAM'],
        'out_of_scope_reason': None,
        'tagging_status': 'review_needed',
        'tagging_confidence': 'medium',
        'tagging_notes': 'krx_unmatched_in_scope',
        'tagged_at': '2026-05-07T08:30:00+00:00',
        'tagger_version': 'langgraph-tagger@2.0',
        'taxonomy_version': 'KRX@2026-05-08',
        'tagging_locked_at': None,
        'tagging_worker_id': None,
    }
    base.update(overrides)
    return base


def test_oos_columns_follow_the_spec():
    assert reports.OOS_CLEARED_COLUMNS == (
        'published_at', 'stock_codes', 'company_names', 'sectors_major', 'sectors_minor', 'products',
    )
    assert reports.OOS_KEPT_COLUMNS == (
        'report_type', 'publisher', 'publisher_type', 'analysts', 'title',
        'stock_codes_raw', 'company_names_raw',
    )


def test_oos_shape_of_a_review_row_is_exact():
    """Same values build_oos_payload wrote (minus tagging_status, which each caller sets)."""
    assert reports.oos_row_shape(make_row(), 'foreign') == {
        'out_of_scope_reason': 'foreign',
        # cleared
        'published_at': None,
        'stock_codes': [],
        'company_names': [],
        'sectors_major': [],
        'sectors_minor': [],
        'products': [],
        # kept from the row
        'report_type': '단일종목',
        'publisher': '삼성증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': '삼성전자 Q1',
        'stock_codes_raw': ['005935'],
        'company_names_raw': ['삼성전자우'],
    }


def test_oos_shape_has_only_the_rule_columns():
    shape = reports.oos_row_shape(make_row(), 'fund')
    assert set(shape) == (
        set(reports.OOS_CLEARED_COLUMNS) | set(reports.OOS_KEPT_COLUMNS) | {'out_of_scope_reason'}
    )
    # Status and identity/file columns belong to the caller, never to the shape.
    for column in ('tagging_status', 'tagging_confidence', 'tagging_notes', 'taxonomy_version',
                   'id', 'message_id', 'file_path', 'file_hash_sha256', 'sent_at'):
        assert column not in shape


def test_oos_shape_from_llm_fields_matches_the_tagger_write():
    """write.py's OOS branch: LLM classification + raw audit kept, body cleared.

    The tagger passes the LLM fields under DB column names (publisher_canon -> publisher).
    """
    llm_fields = {
        'report_type': '단일종목',
        'publisher': '키움증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': '삼성전자 1Q26 Preview',
        'stock_codes_raw': ['TSLA01'],
        'company_names_raw': ['Tesla'],
    }
    shape = reports.oos_row_shape(llm_fields, 'foreign')
    assert shape == {
        'out_of_scope_reason': 'foreign',
        'published_at': None,
        'stock_codes': [], 'company_names': [],
        'sectors_major': [], 'sectors_minor': [], 'products': [],
        'report_type': '단일종목',      # NOT forced to '기타'
        'publisher': '키움증권',
        'publisher_type': 'broker',
        'analysts': ['홍길동'],
        'title': '삼성전자 1Q26 Preview',
        'stock_codes_raw': ['TSLA01'],
        'company_names_raw': ['Tesla'],
    }


@pytest.mark.parametrize("source", [None, {}])
def test_oos_shape_without_any_classification(source):
    """write.py with no LLM output: kept columns fall back to None / []."""
    assert reports.oos_row_shape(source, 'private') == {
        'out_of_scope_reason': 'private',
        'published_at': None,
        'stock_codes': [], 'company_names': [],
        'sectors_major': [], 'sectors_minor': [], 'products': [],
        'report_type': None, 'publisher': None, 'publisher_type': None,
        'analysts': [], 'title': None,
        'stock_codes_raw': [], 'company_names_raw': [],
    }


def test_oos_shape_null_lists_become_empty_lists():
    row = make_row(analysts=None, stock_codes_raw=None, company_names_raw=None)
    shape = reports.oos_row_shape(row, 'digital')
    assert shape['analysts'] == []
    assert shape['stock_codes_raw'] == []
    assert shape['company_names_raw'] == []


def test_oos_shape_copies_lists_and_leaves_the_row_untouched():
    row = make_row()
    before = copy.deepcopy(row)
    shape = reports.oos_row_shape(row, 'ir_self')
    assert row == before
    shape['analysts'].append('누군가')
    shape['stock_codes_raw'].append('000000')
    shape['company_names_raw'].append('X')
    assert row == before
    tuple_row = make_row(analysts=('홍길동',), stock_codes_raw=('005935',))
    shape = reports.oos_row_shape(tuple_row, 'ir_self')
    assert shape['analysts'] == ['홍길동']
    assert shape['stock_codes_raw'] == ['005935']


@pytest.mark.parametrize("reason", ['foreign', 'fund', 'digital', 'private', 'ir_self'])
def test_oos_shape_accepts_every_reason(reason):
    assert reports.oos_row_shape(make_row(), reason)['out_of_scope_reason'] == reason


@pytest.mark.parametrize("reason", ['not_a_real_reason', None, '', 'FOREIGN', 'company'])
def test_oos_shape_rejects_unknown_reason(reason):
    with pytest.raises(ValueError, match="reason must be one of"):
        reports.oos_row_shape(make_row(), reason)


# --- pending-reset row shape -----------------------------------------------------------------

# What features/review/rules.build_pending_reset_payload wrote before the shape moved here,
# key by key and in the same order (the review's retag, "재분류").
OLD_PENDING_RESET = {
    'tagging_status': 'pending',
    'tagging_locked_at': None,
    'tagging_worker_id': None,
    'tagged_at': None,
    'tagging_notes': None,
    'tagging_confidence': None,
    'tagger_version': None,
    'taxonomy_version': None,
    'published_at': None,
    'report_type': None,
    'publisher': None,
    'publisher_type': None,
    'analysts': [],
    'title': None,
    'stock_codes': [],
    'company_names': [],
    'stock_codes_raw': [],
    'company_names_raw': [],
    'sectors_major': [],
    'sectors_minor': [],
    'products': [],
    'out_of_scope_reason': None,
}

# Array columns are NOT NULL in the DB: the reset must write [] there, never None.
ARRAY_COLUMNS = (
    'analysts', 'stock_codes', 'company_names', 'stock_codes_raw', 'company_names_raw',
    'sectors_major', 'sectors_minor', 'products',
)


def test_pending_reset_shape_is_exactly_the_old_retag_payload():
    shape = reports.pending_reset_shape()
    assert shape == OLD_PENDING_RESET
    assert list(shape) == list(OLD_PENDING_RESET)
    assert len(shape) == 22


def test_pending_reset_shape_empties_arrays_and_nulls_everything_else():
    shape = reports.pending_reset_shape()
    assert shape['tagging_status'] == 'pending'
    assert 'pending' in reports.TAGGING_STATUSES
    for column, value in shape.items():
        if column == 'tagging_status':
            continue
        if column in ARRAY_COLUMNS:
            assert value == [], column
        else:
            assert value is None, column
    assert set(ARRAY_COLUMNS) <= set(shape)


def test_pending_reset_shape_covers_every_oos_column():
    """Re-queueing an out-of-scope row clears everything the OOS shape wrote."""
    oos = reports.oos_row_shape(make_row(), 'foreign')
    reset = reports.pending_reset_shape()
    assert set(oos) <= set(reset)
    for column, value in oos.items():
        if isinstance(value, list):
            assert reset[column] == [], column


def test_pending_reset_shape_leaves_collector_columns_alone():
    shape = reports.pending_reset_shape()
    for column in ('id', 'message_id', 'chat_username', 'file_path', 'file_name',
                   'file_size_bytes', 'file_hash_sha256', 'caption', 'downloaded_at', 'sent_at'):
        assert column not in shape


def test_pending_reset_shape_gives_fresh_lists_each_call():
    first = reports.pending_reset_shape()
    first['stock_codes'].append('005930')
    first['analysts'].append('홍길동')
    first['tagging_status'] = 'auto'
    second = reports.pending_reset_shape()
    assert second == OLD_PENDING_RESET
    assert all(second[column] is not first[column] for column in ARRAY_COLUMNS)
