"""The yearly build (spec §5.5, §6.1, §6.3, §6.5, §11) over fakes: targets → profiles (reuse,
extraction, failures) → embeddings → publication → status → retention, plus the pilot, the
one-build-at-a-time rule and the failed close on errors and Ctrl+C."""
from __future__ import annotations

import asyncio
import io
import json
import signal
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from research_desk.domain.stocks import StockEntry, StockList
from research_desk.features.peers import build, logic
from research_desk.features.peers.dart import DartSource
from research_desk.features.peers.schemas import CompanyProfile, Segment
from research_desk.features.peers.settings import PeersSettings
from research_desk.features.peers.store import PeersStore

from .fakes import FakeCollection, FakeLLM, FakeSupabase, pseudo_vector, unit

FY, PV, MODEL = 2025, 'peer-profile@1.0', 'text-embedding-3-large'
SYN = logic.parse_synonyms({'DRAM': ['디램', 'D램']})
START = datetime(2026, 10, 9, 9, 0, tzinfo=timezone.utc)


class Clock:
    """Each reading is one second after the previous one."""

    def __init__(self, start=START):
        self.current = start

    def __call__(self):
        self.current += timedelta(seconds=1)
        return self.current


def cfg(**changes) -> PeersSettings:
    base = dict(profile_model='claude-haiku-5-5', escalation_model='gpt-5.4', profile_version=PV,
                embed_model=MODEL, fiscal_year=FY, max_concurrent_llm=2, per_company_timeout_s=120,
                mongo_url='mongodb://localhost:27017/', mongo_db='FS', mongo_collection='A001_v2')
    return PeersSettings(**(base | changes))


def doc(code, section, *, name, prose='', tables='', rcept=None, parser='0.2.0', cls='K',
        end='2025-12-31'):
    return {'stock_code': code, 'corp_code': f'C{code}', 'corp_name': name, 'corp_cls': cls,
            'rcept_no': rcept or f'R{code}', 'rcept_dt': '20260310', 'report_name': '사업보고서 (2025.12)',
            'fiscal_end': end, 'fiscal_year': FY, 'is_final': True, 'section_code': section,
            'section_title': '', 'segment_label': None, 'prose_text': prose, 'table_text': tables,
            'text': prose, 'parser_version': parser}


def catalog(n):
    """``{code: (name, keywords, products)}`` for n companies whose terms are all in their text."""
    return {f'{i + 1:05d}0': (f'회사{i:02d}', [f'키워드{i}가', f'키워드{i}나'], [f'제품{i}가', f'제품{i}나'])
            for i in range(n)}


class World:
    """A MongoDB, a stock list, a Supabase and an AI client for the companies of ``companies``."""

    def __init__(self, companies, *, db=None, clock=None, extra_docs=(), extra_stocks=()):
        self.companies = dict(companies)
        self.clock = clock or Clock()
        self.db = db or FakeSupabase(now=self.clock)
        self.docs = [d for code, (name, kws, prods) in self.companies.items()
                     for d in self.company_docs(code, name, kws, prods)] + list(extra_docs)
        self.stocks = StockList([StockEntry(code, name, 'KOSDAQ', '전자', '부품', '')
                                 for code, (name, _, _) in self.companies.items()] + list(extra_stocks))
        self.fail: dict[str, BaseException] = {}
        self.llm = FakeLLM(self.reply)
        self.out, self.err = io.StringIO(), io.StringIO()

    @staticmethod
    def company_docs(code, name, keywords, products, rcept=None, parser='0.2.0'):
        overview = f'{name}는 {", ".join(keywords)}를 바탕으로 {", ".join(products)}를 만들어 판매합니다. ' * 3
        table = '품목 | 용도\n' + '\n'.join(f'{p} | 산업용' for p in products)
        return [doc(code, '020100', name=name, prose=overview, rcept=rcept, parser=parser),
                doc(code, '020200', name=name, prose=f'주요 제품은 {", ".join(products)}입니다.',
                    tables=table, rcept=rcept, parser=parser)]

    def name_in(self, user):
        return next(name for name, _, _ in self.companies.values() if f'회사명: {name}\n' in user)

    def reply(self, model, user):
        name = self.name_in(user)
        if name in self.fail:
            return self.fail[name]
        keywords, products = next((k, p) for n, k, p in self.companies.values() if n == name)
        return CompanyProfile(
            niche_industry=f'{name} 틈새', summary=f'{name} 요약', roles=['부품'],
            segments=[Segment(name='주력', products=products[:1], keywords=keywords[:1], revenue_share_pct=80.0),
                      Segment(name='기타', products=products[1:], keywords=keywords[1:], revenue_share_pct=-1)],
            products=list(products), keywords=list(keywords), applications=['산업용'],
            customers=[], competitors=[], is_holding=False, is_financial=False, info_quality='충분')

    def job(self, **changes) -> build.Job:
        settings = changes.pop('settings', None) or cfg()
        base = dict(store=PeersStore(self.db), dart=DartSource(FakeCollection(self.docs)), client=self.llm,
                    stocks=self.stocks, stock_list_version='KRX@2026-05-08', parser_versions=['0.2.0'],
                    synonyms=SYN, cfg=settings, fiscal_year=FY, now=self.clock, out=self.out, err=self.err)
        return build.Job(**(base | changes))

    async def run(self, **changes) -> build.BuildOutcome:
        return await build.run_build(self.job(**changes))

    def summary(self):
        """The last summary JSON printed (it starts on a line of its own)."""
        text = self.out.getvalue()
        start = text.rfind('\n{\n')
        return json.loads(text[0 if start == -1 else start + 1:])

    def set_terms(self, code, terms):
        next(r for r in self.db.tables['company_profiles'] if r['stock_code'] == code)['terms'] = terms

    def build_row(self, build_id=None):
        rows = self.db.rows('peer_builds')
        return rows[-1] if build_id is None else next(r for r in rows if r['build_id'] == build_id)

    def profiles(self, **where):
        return {r['stock_code']: r for r in self.db.rows('company_profiles', **where)}

    def parsed_names(self):
        return [self.name_in(call['user']) for call in self.llm.parse_calls]


# ── a full public build ──────────────────────────────────────────────────────

async def test_a_full_build_profiles_embeds_publishes_and_reports():
    world = World(catalog(3))
    outcome = await world.run()
    assert (outcome.status, outcome.exit_code) == ('done', 0)
    row = world.build_row()
    assert (row['status'], row['eligible'], row['profiled'], row['failed']) == ('done', 3, 3, 0)
    assert (row['fiscal_year'], row['profile_version'], row['embed_model'], row['embed_dims']) == (FY, PV, MODEL, 1536)
    assert row['parser_version'] == '0.2.0' and row['stock_list_version'] == 'KRX@2026-05-08'
    assert row['synonyms_version'] == SYN.fingerprint
    assert row['finished_at'] is not None and row['heartbeat_at'] >= row['started_at']
    assert [p for p, _ in row['company_quantiles']] == list(logic.PERCENTILES)
    assert [p for p, _ in row['segment_quantiles']] == list(logic.PERCENTILES)
    assert row['term_table']['키워드0가'] == {'display': '키워드0가', 'companies': 1}
    profiles = world.profiles()
    assert set(profiles) == {'000010', '000020', '000030'}
    assert all(p['status'] == 'ok' for p in profiles.values())
    assert profiles['000010']['terms'] == ['키워드0가', '키워드0나', '제품0가', '제품0나']
    company = world.db.rows('company_embeddings')
    segments = world.db.rows('segment_embeddings')
    assert len(company) == 3 and len(segments) == 6
    assert all(e['embed_model'] == MODEL for e in company + segments)
    assert all(np.linalg.norm(e['embedding']) == pytest.approx(1.0) for e in company + segments)
    assert all(call['dimensions'] == 1536 and call['model'] == MODEL for call in world.llm.embed_calls)
    summary = world.summary()
    assert summary['status'] == 'done' and summary['build_id'] == row['build_id']
    assert (summary['eligible'], summary['profiled'], summary['failed'], summary['reused']) == (3, 3, 0, 0)
    assert summary['tokens']['input'] == 300 and summary['tokens']['output'] == 30
    assert summary['tokens']['embedding'] > 0


async def test_the_profile_row_records_its_source_input_and_ai_call():
    world = World(catalog(1))
    await world.run()
    row = world.profiles()['000010']
    assert (row['corp_code'], row['corp_name'], row['rcept_no']) == ('C000010', '회사00', 'R000010')
    assert (row['report_name'], row['fiscal_end'], row['parser_version']) == ('사업보고서 (2025.12)', '2025-12-31', '0.2.0')
    assert row['source_sections'] == ['020100', '020200'] and row['input_truncated'] is False
    assert row['source_chars'] > 0
    assert row['profile']['keywords'] == ['키워드0가', '키워드0나']
    assert row['one_line'] == '회사00 틈새'
    assert (row['is_holding'], row['is_financial'], row['info_quality']) == (False, False, '충분')
    assert (row['llm_model'], row['input_tokens'], row['output_tokens']) == ('claude-haiku-5-5', 100, 10)
    assert row['grounding_ratio'] == 1.0 and row['fail_reason'] is None
    segments = world.db.rows('company_segments', stock_code='000010')
    assert [(s['seg_no'], s['name'], s['revenue_share_pct']) for s in segments] == [(0, '주력', 80.0), (1, '기타', -1)]


async def test_shared_terms_are_counted_across_companies_with_the_standard_spelling():
    companies = {'000010': ('가나반도체', ['Legacy DRAM', 'D램'], ['MCP']),
                 '000020': ('다라메모리', ['legacy-dram', '디램'], ['eMMC'])}
    world = World(companies)
    await world.run()
    table = world.build_row()['term_table']
    assert table['legacydram'] == {'display': 'Legacy DRAM', 'companies': 2}
    assert table['dram'] == {'display': 'DRAM', 'companies': 2}
    assert table['mcp'] == {'display': 'MCP', 'companies': 1}


# ── status: done / incomplete / pilot ────────────────────────────────────────

async def test_95_percent_of_targets_profiled_is_done():
    world = World(catalog(20))
    world.fail['회사07'] = RuntimeError('AI down')
    outcome = await world.run()
    assert (outcome.status, outcome.exit_code) == ('done', 0)
    row = world.build_row()
    assert (row['eligible'], row['profiled'], row['failed']) == (20, 19, 1)
    failed = world.profiles()['000080']
    assert failed['status'] == 'failed' and 'AI down' in failed['fail_reason']


async def test_below_95_percent_is_incomplete_and_publishes_nothing():
    world = World(catalog(20))
    for name in ('회사03', '회사07'):
        world.fail[name] = RuntimeError('AI down')
    outcome = await world.run()
    assert (outcome.status, outcome.exit_code) == ('incomplete', 1)
    row = world.build_row()
    assert (row['status'], row['profiled'], row['failed']) == ('incomplete', 18, 2)
    assert row['term_table'] is None and row['company_quantiles'] is None
    assert world.summary()['status'] == 'incomplete'


async def test_an_incomplete_build_leaves_the_terms_column_alone():
    world = World(catalog(2))
    await world.run()
    world.set_terms('000010', ['stale'])
    world.fail['회사01'] = RuntimeError('AI down')
    world.docs = [d if d['stock_code'] != '000020' else d | {'rcept_no': 'R-new'} for d in world.docs]
    outcome = await world.run()
    assert outcome.status == 'incomplete'
    assert world.profiles()['000010']['terms'] == ['stale']


async def test_a_pilot_is_recorded_as_pilot_prints_neighbours_and_keeps_the_terms():
    world = World(catalog(4))
    await world.run()
    world.set_terms('000010', ['stale'])
    world.out = io.StringIO()
    calls = len(world.llm.parse_calls)
    outcome = await world.run(pilot=True)
    assert (outcome.status, outcome.exit_code) == ('pilot', 0)
    assert len(world.llm.parse_calls) == calls                # profiles reused
    row = world.build_row()
    assert row['status'] == 'pilot' and row['term_table'] is None and row['company_quantiles'] is None
    assert world.profiles()['000010']['terms'] == ['stale']
    blocks = world.out.getvalue().split('■ ')[1:]
    assert [b.split(' — ')[0] for b in blocks] == ['000010 회사00', '000020 회사01', '000030 회사02',
                                                   '000040 회사03']
    first = blocks[0]
    companies = first[first.index('회사 유사도 상위 10'):first.index('부문 유사도 상위 10')]
    listed = [line.split()[1] for line in companies.splitlines()[1:] if line.strip()]
    assert sorted(listed) == ['000020', '000030', '000040']    # every other company, not the seed
    segments = first[first.index('부문 유사도 상위 10'):]
    assert '000010' not in segments.split('\n', 1)[1]
    assert world.summary()['status'] == 'pilot'


async def test_a_pilot_stores_profiles_that_the_next_public_build_reuses():
    world = World(catalog(3))
    await world.run(pilot=True)
    calls, embeds = len(world.llm.parse_calls), len(world.llm.embed_calls)
    outcome = await world.run()
    assert outcome.status == 'done'
    assert len(world.llm.parse_calls) == calls       # nothing extracted again
    assert len(world.llm.embed_calls) == embeds      # nothing embedded again
    assert world.summary()['reused'] == 3
    assert len(world.db.rows('company_embeddings')) == 3


# ── reuse and retries ────────────────────────────────────────────────────────

async def test_a_profile_is_reused_only_with_the_same_report_parser_and_profile_version():
    world = World(catalog(4))
    await world.run()
    assert sorted(world.parsed_names()) == ['회사00', '회사01', '회사02', '회사03']
    world.llm.parse_calls.clear()
    # 000020: a corrected report; 000030: a new parser version; 000040: unchanged.
    world.docs = ([d for d in world.docs if d['stock_code'] in ('000010', '000040')]
                  + World.company_docs('000020', '회사01', *world.companies['000020'][1:], rcept='R-corrected')
                  + World.company_docs('000030', '회사02', *world.companies['000030'][1:], parser='0.2.1'))
    outcome = await world.run(parser_versions=['0.2.0', '0.2.1'])
    assert sorted(world.parsed_names()) == ['회사01', '회사02']
    assert outcome.summary['reused'] == 2
    profiles = world.profiles()
    assert profiles['000020']['rcept_no'] == 'R-corrected' and profiles['000030']['parser_version'] == '0.2.1'


async def test_a_new_profile_version_extracts_again_and_leaves_the_old_rows():
    world = World(catalog(2))
    await world.run()
    old = world.profiles(profile_version=PV)
    world.llm.parse_calls.clear()
    outcome = await world.run(settings=cfg(profile_version='peer-profile@2.0'))
    assert outcome.status == 'done'
    assert sorted(world.parsed_names()) == ['회사00', '회사01']
    assert world.profiles(profile_version=PV) == old
    assert set(world.profiles(profile_version='peer-profile@2.0')) == {'000010', '000020'}


async def test_a_rebuilt_profile_replaces_its_segments_and_embeddings():
    world = World(catalog(2))
    await world.run()
    old_vector = world.db.rows('company_embeddings', stock_code='000010')[0]['embedding']
    world.companies['000010'] = ('회사00', ['새키워드가'], ['새제품가'])
    world.docs = ([d for d in world.docs if d['stock_code'] != '000010']
                  + World.company_docs('000010', '회사00', ['새키워드가'], ['새제품가'], rcept='R-2'))
    await world.run()
    assert world.profiles()['000010']['profile']['keywords'] == ['새키워드가']
    assert [s['products'] for s in world.db.rows('company_segments', stock_code='000010')] == [['새제품가'], []]
    new = world.db.rows('company_embeddings', stock_code='000010')
    assert len(new) == 1 and new[0]['embedding'] != old_vector
    assert len(world.db.rows('segment_embeddings', stock_code='000010')) == 2


async def test_only_the_failed_companies_are_tried_again():
    world = World(catalog(3))
    world.fail['회사01'] = RuntimeError('AI down')
    await world.run()
    world.fail.clear()
    world.llm.parse_calls.clear()
    outcome = await world.run()
    assert world.parsed_names() == ['회사01']
    assert outcome.status == 'done' and world.profiles()['000020']['status'] == 'ok'


async def test_a_company_without_text_fails_without_an_ai_call():
    world = World(catalog(1), extra_docs=[doc('000990', '020100', name='빈회사', prose='  ')],
                  extra_stocks=[StockEntry('000990', '빈회사', 'KOSDAQ', '전자', '부품', '')])
    await world.run()
    row = world.profiles()['000990']
    assert row['status'] == 'failed' and '본문' in row['fail_reason']
    assert world.parsed_names() == ['회사00']


async def test_companies_outside_the_targets_never_reach_the_ai():
    world = World(catalog(1), extra_docs=[doc('000990', '020100', name='마바스팩', prose='스팩 본문'),
                                          doc('000991', '020100', name='코넥스사', prose='본문', cls='N')],
                  extra_stocks=[StockEntry('000990', '마바스팩', 'KOSDAQ', '금융', '스팩', ''),
                                StockEntry('000991', '코넥스사', 'KONEX', '전자', '부품', '')])
    outcome = await world.run()
    assert outcome.summary['eligible'] == 1
    assert all('마바스팩' not in c['user'] and '코넥스사' not in c['user'] for c in world.llm.parse_calls)
    assert set(world.profiles()) == {'000010'}


async def test_a_company_over_its_time_limit_fails_alone():
    world = World(catalog(3))

    async def slow_parse(**kwargs):
        if '회사01' in kwargs['user']:
            await asyncio.sleep(5)
        return await FakeLLM.parse(world.llm, **kwargs)

    world.llm.parse = slow_parse
    outcome = await world.run(settings=cfg(per_company_timeout_s=0.05))
    profiles = world.profiles()
    assert profiles['000020']['status'] == 'failed' and '시간 초과' in profiles['000020']['fail_reason']
    assert profiles['000010']['status'] == 'ok' and profiles['000030']['status'] == 'ok'
    assert outcome.summary['failed'] == 1


@pytest.mark.parametrize('limit, expected', [(2, 2), (1, 1)])
async def test_ai_calls_in_flight_never_exceed_the_setting(limit, expected):
    world = World(catalog(8))
    world.llm.delay = 0.01
    await world.run(settings=cfg(max_concurrent_llm=limit))
    assert world.llm.max_active == expected


async def test_progress_is_written_while_the_build_runs():
    world = World(catalog(3))
    await world.run()
    updates = [op.payload for op in world.db.ops('update', 'peer_builds')]
    progress = [u for u in updates if 'heartbeat_at' in u and 'status' not in u]
    assert len(progress) >= 3
    assert [u['profiled'] for u in progress if 'profiled' in u][:3] == [1, 2, 3]
    stamps = [u['heartbeat_at'] for u in progress]
    assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)


# ── targets ──────────────────────────────────────────────────────────────────

async def test_codes_and_limit_reduce_the_targets_and_the_build_records_that_count():
    world = World(catalog(5))
    outcome = await world.run(codes=['000020', '000040', '000050', '999999'])
    assert outcome.status == 'done' and world.build_row()['eligible'] == 3
    assert '999999' in world.err.getvalue()
    world.llm.parse_calls.clear()
    outcome = await world.run(limit=2)
    assert world.build_row()['eligible'] == 2
    assert world.parsed_names() == ['회사00']           # 000020 was profiled by the first run


async def test_no_targets_fails_the_build():
    world = World(catalog(2))
    outcome = await world.run(codes=['999999'])
    assert (outcome.status, outcome.exit_code) == ('failed', 1)
    row = world.build_row()
    assert row['status'] == 'failed' and '대상' in row['message']
    assert world.llm.parse_calls == []


# ── embeddings ───────────────────────────────────────────────────────────────

async def test_embeddings_are_made_100_at_a_time_and_only_where_missing():
    world = World(catalog(101))
    await world.run()
    company_batches = [len(c['texts']) for c in world.llm.embed_calls if c['texts'][0].startswith('업종:')]
    segment_batches = [len(c['texts']) for c in world.llm.embed_calls if not c['texts'][0].startswith('업종:')]
    assert company_batches == [100, 1]
    assert segment_batches == [100, 100, 2]
    world.llm.embed_calls.clear()
    await world.run()
    assert world.llm.embed_calls == []


async def test_a_new_embedding_model_adds_rows_and_leaves_the_old_models_rows():
    world = World(catalog(3))
    await world.run()
    old = world.db.rows('company_embeddings') + world.db.rows('segment_embeddings')
    outcome = await world.run(settings=cfg(embed_model='text-embedding-3-small'))
    assert outcome.status == 'done'
    assert world.db.rows('company_embeddings', embed_model=MODEL) + world.db.rows(
        'segment_embeddings', embed_model=MODEL) == old
    assert len(world.db.rows('company_embeddings', embed_model='text-embedding-3-small')) == 3
    assert len(world.db.rows('segment_embeddings', embed_model='text-embedding-3-small')) == 6
    assert sorted(world.parsed_names()) == ['회사00', '회사01', '회사02']    # profiles reused


# ── one build at a time ──────────────────────────────────────────────────────

def running_build(heartbeat):
    return {'fiscal_year': FY, 'profile_version': PV, 'embed_model': MODEL, 'status': 'running',
            'started_at': heartbeat.isoformat(), 'heartbeat_at': heartbeat.isoformat()}


async def test_a_running_build_with_progress_in_the_last_six_hours_refuses_a_new_one():
    clock = Clock()
    db = FakeSupabase(now=clock, peer_builds=[running_build(START - timedelta(hours=6) + timedelta(seconds=1))])
    world = World(catalog(2), db=db, clock=clock)
    outcome = await world.run()
    assert (outcome.status, outcome.exit_code) == ('refused', 1)
    assert [b['status'] for b in db.rows('peer_builds')] == ['running']
    assert '진행 중' in world.err.getvalue() and world.llm.parse_calls == []


async def test_a_running_build_silent_for_over_six_hours_is_failed_and_taken_over():
    clock = Clock()
    db = FakeSupabase(now=clock, peer_builds=[running_build(START - timedelta(hours=6))])
    world = World(catalog(2), db=db, clock=clock)
    outcome = await world.run()          # the clock reads START + 1s: 6 h 1 s of silence
    assert outcome.status == 'done'
    old, new = db.rows('peer_builds')
    assert old['status'] == 'failed' and '6시간' in old['message'] and old['finished_at']
    assert new['status'] == 'done'


# ── retention ────────────────────────────────────────────────────────────────

def done_build(finished, pv=PV, model=MODEL):
    return {'fiscal_year': FY, 'profile_version': pv, 'embed_model': model, 'status': 'done',
            'finished_at': finished}


async def test_only_the_three_latest_public_builds_and_the_rows_they_use_are_kept():
    clock = Clock()
    old_profile = {'fiscal_year': FY, 'profile_version': 'peer-profile@0.9', 'stock_code': '000010',
                   'status': 'ok'}
    db = FakeSupabase(
        now=clock,
        peer_builds=[done_build('2026-01-01T00:00:00+00:00', pv='peer-profile@0.9'),
                     done_build('2026-02-01T00:00:00+00:00', model='old-model'),
                     done_build('2026-03-01T00:00:00+00:00')],
        company_profiles=[old_profile],
    )
    world = World(catalog(2), db=db, clock=clock)
    await world.run(settings=cfg(embed_model='old-model'))    # build 4: uses old-model too
    await world.run()                                          # build 5
    assert [b['build_id'] for b in db.rows('peer_builds')] == [3, 4, 5]
    assert world.profiles(profile_version='peer-profile@0.9') == {}     # only build 1 used it
    # old-model rows stay: build 4 (kept) uses them.
    assert len(db.rows('company_embeddings', embed_model='old-model')) == 2
    await world.run(settings=cfg(embed_model='newest'))                 # build 6 → build 3 goes
    await world.run(settings=cfg(embed_model='newest'))                 # build 7 → build 4 goes
    assert [b['build_id'] for b in db.rows('peer_builds')] == [5, 6, 7]
    # ...but under the current profile version every row stays, whatever its model.
    assert len(db.rows('company_embeddings', embed_model='old-model')) == 2


async def test_old_profile_versions_used_by_no_build_are_removed_but_not_the_current_one():
    clock = Clock()
    db = FakeSupabase(now=clock, company_profiles=[
        {'fiscal_year': FY, 'profile_version': 'peer-profile@0.1', 'stock_code': '000010', 'status': 'ok'},
        {'fiscal_year': 2024, 'profile_version': PV, 'stock_code': '000010', 'status': 'ok'}])
    world = World(catalog(1), db=db, clock=clock)
    await world.run()
    assert set((r['fiscal_year'], r['profile_version']) for r in db.rows('company_profiles')) == {
        (2024, PV), (FY, PV)}


# ── errors and interruptions ─────────────────────────────────────────────────

def test_an_error_while_running_fails_the_build_and_exits_1():
    world = World(catalog(2))

    async def broken(**kwargs):
        raise RuntimeError('embedding service down')

    world.llm.embed = broken
    assert build.execute(world.job()) == 1
    row = world.build_row()
    assert row['status'] == 'failed' and 'embedding service down' in row['message']
    assert row['finished_at'] is not None
    assert '실패' in world.err.getvalue()


def test_ctrl_c_fails_the_build_and_exits_1():
    """A real SIGINT: asyncio.run cancels the build's task, then raises KeyboardInterrupt."""
    world = World(catalog(3))
    pressed = []

    def press_ctrl_c(model, user):
        if not pressed:
            pressed.append(True)
            signal.raise_signal(signal.SIGINT)
        return world.reply(model, user)

    world.llm.reply = press_ctrl_c
    assert build.execute(world.job()) == 1
    row = world.build_row()
    assert row['status'] == 'failed' and '중단' in row['message'] and row['finished_at']
    assert '중단' in world.err.getvalue()
    assert world.llm.closed


def test_a_keyboard_interrupt_inside_the_work_also_fails_the_build():
    """A second Ctrl+C raises KeyboardInterrupt wherever the program happens to be."""
    world = World(catalog(2))
    world.fail['회사00'] = KeyboardInterrupt()
    assert build.execute(world.job()) == 1
    row = world.build_row()
    assert row['status'] == 'failed' and '중단' in row['message']
    assert '중단' in world.err.getvalue()


def test_a_cancelled_build_is_failed_and_exits_1():
    """asyncio.run turns the first Ctrl+C into a cancellation of the build's task."""
    world = World(catalog(2))

    def cancel_main(model, user):
        for task in asyncio.all_tasks():
            if task.get_coro().__name__ == 'run_build':
                task.cancel()
        return world.reply(model, user)

    world.llm.reply = cancel_main
    assert build.execute(world.job()) == 1
    assert world.build_row()['status'] == 'failed'


def test_a_finished_build_is_not_touched_by_a_later_error():
    world = World(catalog(2))
    assert build.execute(world.job()) == 0
    assert world.build_row()['status'] == 'done'


def test_an_interruption_during_the_clean_up_keeps_the_published_build():
    world = World(catalog(2))
    job = world.job()

    def interrupted():
        raise KeyboardInterrupt

    job.store.builds = interrupted          # retention reads the builds first
    assert build.execute(job) == 1
    assert world.build_row()['status'] == 'done'
    assert '그 전에 done 상태로 마감' in world.err.getvalue()


def test_a_clean_up_error_after_publishing_exits_1_and_keeps_the_build():
    world = World(catalog(2))
    job = world.job()

    def broken():
        raise RuntimeError('db hiccup')

    job.store.builds = broken
    assert build.execute(job) == 1
    assert world.build_row()['status'] == 'done'
    assert 'db hiccup' in world.err.getvalue() and world.summary()['status'] == 'done'


def test_an_interruption_before_the_build_starts_leaves_no_build():
    world = World(catalog(2))
    job = world.job()

    def interrupted():
        raise KeyboardInterrupt

    job.store.running_builds = interrupted
    assert build.execute(job) == 1
    assert world.db.rows('peer_builds') == []
    assert '빌드는 시작하지 않았습니다' in world.err.getvalue()


def test_an_error_message_hides_the_password_of_a_url():
    assert build.describe(RuntimeError('cannot reach mongodb://reader:s3cret@db.local:27017/')) == (
        'RuntimeError: cannot reach mongodb://***@db.local:27017/')


def test_exit_codes_follow_the_status():
    world = World(catalog(1))
    assert build.execute(world.job(pilot=True)) == 0
    world.fail['회사00'] = RuntimeError('AI down')
    world.docs = World.company_docs('000010', '회사00', *world.companies['000010'][1:], rcept='R-new')
    assert build.execute(world.job()) == 1      # 0 of 1 profiled: incomplete
    assert world.build_row()['status'] == 'incomplete'
