"""Business-report source (spec §4): parser versions, the fiscal-year rule, target companies and
section texts, over a fake MongoDB collection."""
from __future__ import annotations

import pytest

from research_desk.domain.stocks import StockEntry, StockList
from research_desk.features.peers import dart
from research_desk.features.peers.logic import Section

from .fakes import FakeCollection


def doc(code, section='020100', *, name='가나반도체', cls='K', rcept='20260310000001', fy=2025,
        end='2025-12-31', final=True, parser='0.2.0', prose='본문', tables='', rcept_dt='20260310',
        report='사업보고서 (2025.12)', corp_code='00100001'):
    return {'stock_code': code, 'corp_code': corp_code, 'corp_name': name, 'corp_cls': cls,
            'rcept_no': rcept, 'rcept_dt': rcept_dt, 'report_name': report, 'fiscal_end': end,
            'fiscal_year': fy, 'is_final': final, 'section_code': section, 'section_title': '제목',
            'segment_label': None, 'prose_text': prose, 'table_text': tables,
            'text': prose + '\n\n' + tables, 'parser_version': parser}


def stocks(*entries) -> StockList:
    return StockList([StockEntry(code, name, 'KOSDAQ', '반도체', '메모리', '') for code, name in entries])


STOCKS = stocks(('000010', '가나반도체'), ('000020', '다라전자'), ('000030', '마바스팩'),
                ('000040', '사아리츠'), ('000050', '자차기업인수목적'), ('000060', '카타부동산투자회사'),
                ('000070', '코넥스회사'), ('00007A', '영문코드회사'), ('000080', '기타법인'))


@pytest.mark.parametrize('value, parsed', [
    ('0.2.0', (0, 2, 0)), ('0.10.1', (0, 10, 1)), ('1.0', (1, 0)), ('v0.3.0', (0, 3, 0)),
    ('0.2.0rc1', (0, 2, 0)), ('abc', None), (None, None), ('', None),
])
def test_parser_versions_are_read_as_numbers(value, parsed):
    assert dart.parse_version(value) == parsed


@pytest.mark.parametrize('value, ok', [('0.2.0', True), ('0.2', True), ('0.10.0', True), ('1.0.0', True),
                                       ('0.1.9', False), ('0.1.0', False), ('x', False), (None, False)])
def test_only_parser_versions_from_0_2_0_count(value, ok):
    assert dart.is_supported_parser(value) is ok


def test_the_usable_parser_versions_of_a_year():
    source = dart.DartSource(FakeCollection([
        doc('000010', parser='0.1.0'), doc('000020', parser='0.2.1'), doc('000030', parser='0.10.0'),
        doc('000040', parser='0.2.1', section='020200'), doc('000050', parser='0.3.0', fy=2024),
    ]))
    assert source.parser_versions(2025) == ['0.2.1', '0.10.0']
    assert source.parser_versions(2023) == []


def test_each_company_uses_its_final_report_of_the_year_with_the_latest_fiscal_end():
    docs = [
        doc('000010', rcept='A-old', end='2025-03-31', rcept_dt='20250620'),
        doc('000010', rcept='A-new', end='2025-12-31', rcept_dt='20260310'),
        doc('000010', rcept='A-draft', end='2026-03-31', final=False),        # not final
        doc('000010', rcept='A-2026', end='2026-12-31', fy=2026),             # other year
        doc('000010', '020200', rcept='A-new', end='2025-12-31', rcept_dt='20260310'),
        # A March year-end company: its 2025-03-31 report belongs to fiscal year 2025.
        doc('000020', name='다라전자', rcept='B-mar', end='2025-03-31', rcept_dt='20250620'),
    ]
    reports = dart.DartSource(FakeCollection(docs)).reports(2025, STOCKS, ['0.2.0'])
    assert [(r.stock_code, r.rcept_no, r.fiscal_end) for r in reports] == [
        ('000010', 'A-new', '2025-12-31'), ('000020', 'B-mar', '2025-03-31')]
    first = reports[0]
    assert first.section_codes == ('020100', '020200')
    assert (first.corp_name, first.corp_code, first.report_name, first.parser_version) == (
        '가나반도체', '00100001', '사업보고서 (2025.12)', '0.2.0')


def test_targets_are_kospi_and_kosdaq_companies_in_the_stock_list_without_spac_or_reit_names():
    docs = [
        doc('000010', cls='Y'), doc('000020', name='다라전자', cls='K'),
        doc('000030', name='마바스팩', cls='K'),                      # SPAC
        doc('000040', name='사아리츠', cls='Y'),                      # REIT
        doc('000050', name='자차기업인수목적', cls='K'),
        doc('000060', name='카타부동산투자회사', cls='Y'),
        doc('000070', name='코넥스회사', cls='N'),                    # KONEX
        doc('000080', name='기타법인', cls='E'),
        doc('000090', name='종목표에없음', cls='K'),                   # not in the stock list
        doc('00007A', name='영문코드회사', cls='K'),                   # letters in the code are fine
        doc('12345', name='코드이상', cls='K'),                         # not a 6-character code
    ]
    reports = dart.DartSource(FakeCollection(docs)).reports(2025, STOCKS, ['0.2.0'])
    assert [r.stock_code for r in reports] == ['000010', '000020', '00007A']


def test_a_banned_word_in_the_stock_list_name_also_excludes():
    docs = [doc('000030', name='마바주식회사', cls='K')]     # DART name clean, stock list name 마바스팩
    assert dart.DartSource(FakeCollection(docs)).reports(2025, STOCKS, ['0.2.0']) == []


def test_documents_below_parser_0_2_0_are_not_read():
    docs = [doc('000010', parser='0.1.0'), doc('000020', name='다라전자', parser='0.2.0')]
    collection = FakeCollection(docs)
    reports = dart.DartSource(collection).reports(2025, STOCKS, ['0.2.0'])
    assert [r.stock_code for r in reports] == ['000020']
    query, projection = collection.finds[0]
    assert query == {'fiscal_year': 2025, 'is_final': True, 'parser_version': {'$in': ['0.2.0']}}
    assert 'prose_text' not in projection and 'table_text' not in projection   # no text yet


def test_a_report_whose_sections_have_two_parser_versions_records_the_newest():
    docs = [doc('000010', parser='0.2.0'), doc('000010', '020200', parser='0.10.0')]
    reports = dart.DartSource(FakeCollection(docs)).reports(2025, STOCKS, ['0.2.0', '0.10.0'])
    assert reports[0].parser_version == '0.10.0'


def test_the_sections_of_one_report_are_read_when_needed():
    docs = [doc('000010', '020100', prose='개요', tables='표1'),
            doc('000010', '020200', prose='제품', tables='표2'),
            doc('000010', '020300', prose='원재료'),                    # not an input section
            doc('000010', '020100', rcept='other', prose='다른 보고서', final=False)]
    collection = FakeCollection(docs)
    source = dart.DartSource(collection)
    report = source.reports(2025, STOCKS, ['0.2.0'])[0]
    sections = source.sections(report)
    assert sections == {'020100': Section(prose='개요', tables='표1'),
                        '020200': Section(prose='제품', tables='표2')}
    query, _ = collection.finds[-1]
    assert query['rcept_no'] == report.rcept_no and query['stock_code'] == '000010'


def test_missing_text_fields_read_as_empty():
    docs = [doc('000010')]
    del docs[0]['table_text']
    docs[0]['prose_text'] = None
    source = dart.DartSource(FakeCollection(docs))
    report = source.reports(2025, STOCKS, ['0.2.0'])[0]
    assert source.sections(report) == {'020100': Section(prose='', tables='')}


def test_closing_the_source_closes_its_client():
    collection = FakeCollection([])
    dart.DartSource(collection).close()
    assert collection.closed
