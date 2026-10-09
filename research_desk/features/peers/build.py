"""The yearly build behind ``peers build`` (spec §5.5, §6.1, §6.3, §6.5, §11).

A build is one run for (fiscal year, profile version, embedding model):

1. One build at a time: a ``running`` build with progress in the last six hours refuses this one
   (exit 1); a running build silent for longer is closed as ``failed`` and this one starts.
2. Targets: each target company's report of the year (``dart``), narrowed by ``--codes`` /
   ``--limit`` (the build records the smaller count as it is).
3. Profiles: an ``ok`` profile with the same report (``rcept_no``) and parser version is reused;
   every other target is extracted again (failed ones included), at most
   ``PEERS_MAX_CONCURRENT_LLM`` companies at once, each within ``PEERS_PER_COMPANY_TIMEOUT_S``.
   A company whose extraction fails is written as ``failed`` with its reason; the build goes on.
   Companies outside the targets (KONEX, SPACs, REITs, not in the stock list) never reach the AI.
4. Embeddings: every ``ok`` profile of the fiscal year and profile version, and every segment of
   it, that has no embedding of this build's model gets one (100 texts per call, 1536
   dimensions, scaled to length 1). Other models' embeddings are never touched.
5. Status: ``pilot`` for a pilot; ``done`` when profiles succeeded for 95 % of the targets; else
   ``incomplete`` (run again to continue). Only a ``done`` build rewrites the ``terms`` of every
   ``ok`` profile and records the term table and the percentile tables; a pilot prints each
   target's ten most similar companies and segments instead and leaves ``terms`` alone.
6. Retention: the three latest public builds stay; older ones, and the profile and embedding rows
   neither a remaining build nor the current profile version uses, are deleted.
7. A summary JSON on stdout. Exit code: done 0, pilot 0, incomplete 1; refused 1.

The progress time (``heartbeat_at``) moves after every company and embedding batch. An error or
an interruption (Ctrl+C) while the build runs closes it as ``failed`` and the command ends with 1;
a process that dies before that is cleaned up by the six-hour rule. ``run_build`` closes the AI
client it was given when it ends.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence, TextIO

from research_desk.core.llm import call_with_retry
from research_desk.domain.stocks import StockList

from . import logic
from .dart import CompanyReport, DartSource, parse_version
from .llm import extract_profile
from .settings import PeersSettings
from .store import PeersStore

logger = logging.getLogger(__name__)

EMBED_BATCH = 100
EMBED_DIMS = 1536
EMBED_TIMEOUT_S = 60.0
NEIGHBOURS = 10
REASON_MAX = 300

EXIT_CODES = {"done": 0, "pilot": 0, "incomplete": 1, "failed": 1, "refused": 1}

ANOTHER_BUILD_RUNNING = ("다른 유사도 계산(빌드 {build_id})이 진행 중입니다(마지막 진행 {heartbeat}). "
                         "끝난 뒤 다시 실행하세요.")
STALE_BUILD = "6시간 넘게 진행이 없어 실패로 정리했습니다."
NO_TARGETS = "대상 회사가 없습니다."
NOT_TARGETS = "대상이 아니어서 뺀 종목: {codes}"
INCOMPLETE = "프로필 성공 {profiled}/{eligible}: 95% 미만이라 공개하지 않았습니다. 다시 실행하면 이어서 합니다."
INTERRUPTED = "중단됨(Ctrl+C 등)"
ERROR = "오류로 중단: {error}"
EMPTY_INPUT = "사업보고서 본문이 비어 있습니다"
TIMEOUT = "시간 초과({seconds:g}초)"
AI_ERROR = "AI 오류: {error}"
LINE_INTERRUPTED = "유사도 계산이 중단되었습니다. {note}"
LINE_FAILED = "유사도 계산 중 오류가 났습니다({error}). {note}"
NOTE_NO_BUILD = "빌드는 시작하지 않았습니다."
NOTE_FAILED = "빌드 번호 {build_id}의 상태를 실패(failed)로 바꿨습니다."
NOTE_NOT_CLOSED = ("빌드 번호 {build_id}의 상태를 바꾸지 못했습니다. 6시간이 지나면 다음 실행이 "
                   "실패로 정리합니다.")
NOTE_ALREADY_CLOSED = "빌드 번호 {build_id}는 그 전에 {status} 상태로 마감됐습니다."
LINE_CLEANUP_FAILED = "빌드는 마쳤지만 오래된 빌드 정리 중 오류가 났습니다: {error}"

_URL_CREDENTIALS = re.compile(r"(\w+://)[^/@\s]+@")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def describe(exc: BaseException) -> str:
    """``Type: message`` of an error, short, with any ``user:password@`` of a URL masked."""
    text = _URL_CREDENTIALS.sub(r"\1***@", str(exc)).strip()
    return f"{type(exc).__name__}: {text}"[:REASON_MAX] if text else type(exc).__name__


def _instant(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass
class Job:
    """Everything one build uses. ``client`` is the AI client (``core.llm.LLMClient``)."""
    store: PeersStore
    dart: DartSource
    client: Any
    stocks: StockList
    stock_list_version: str
    parser_versions: Sequence[str]
    synonyms: logic.Synonyms
    cfg: PeersSettings
    fiscal_year: int
    codes: Optional[Sequence[str]] = None
    limit: Optional[int] = None
    pilot: bool = False
    now: Callable[[], datetime] = utc_now
    out: Optional[TextIO] = None
    err: Optional[TextIO] = None


@dataclass
class BuildState:
    """What the caller can see of a build (to close it after an interruption)."""
    build_id: Optional[int] = None
    status: Optional[str] = None          # the status it was closed with; None while it runs

    @property
    def finalized(self) -> bool:
        return self.status is not None

    def note(self) -> str:
        """What became of the build, for the last stderr line."""
        if self.build_id is None:
            return NOTE_NO_BUILD
        if self.status == "failed":
            return NOTE_FAILED.format(build_id=self.build_id)
        if self.status is None:
            return NOTE_NOT_CLOSED.format(build_id=self.build_id)
        return NOTE_ALREADY_CLOSED.format(build_id=self.build_id, status=self.status)


@dataclass(frozen=True)
class BuildOutcome:
    status: str                        # done / incomplete / pilot / failed / refused
    exit_code: int
    build_id: Optional[int] = None
    summary: Optional[dict] = None


@dataclass
class _Tally:
    profiled: int = 0
    failed: int = 0
    reused: int = 0
    extracted: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    embedding_tokens: int = 0
    embedded: dict = field(default_factory=lambda: {"companies": 0, "segments": 0})


async def _run_workers(items: Sequence[Any], count: int, handle: Callable[[Any], Awaitable[None]]) -> None:
    """``handle`` every item with at most ``count`` running at once; the first error cancels
    the rest and is raised."""
    queue = list(items)

    async def worker() -> None:
        while queue:
            await handle(queue.pop(0))

    tasks = [asyncio.create_task(worker()) for _ in range(min(count, len(queue)))]
    if not tasks:
        return
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if not task.cancelled() and task.exception() is not None:
                raise task.exception()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class _Build:
    def __init__(self, job: Job, state: BuildState) -> None:
        self.job, self.state = job, state
        self.store, self.cfg = job.store, job.cfg
        self.out, self.err = job.out or sys.stdout, job.err or sys.stderr
        self.fy, self.pv, self.model = job.fiscal_year, job.cfg.profile_version, job.cfg.embed_model
        self.tally = _Tally()

    # ── small helpers ────────────────────────────────────────────────────────

    def stamp(self) -> str:
        return self.job.now().isoformat()

    def progress(self, **fields: Any) -> None:
        self.store.update_build(self.state.build_id, {**fields, "heartbeat_at": self.stamp()},
                                only_running=True)

    def finish(self, status: str, **fields: Any) -> None:
        stamp = self.stamp()
        self.store.update_build(self.state.build_id, {**fields, "status": status, "finished_at": stamp,
                                                      "heartbeat_at": stamp})
        self.state.status = status

    # ── 1. one build at a time ───────────────────────────────────────────────

    def refusal(self) -> Optional[str]:
        now = self.job.now()
        running = self.store.running_builds()
        fresh = [b for b in running if not logic.is_stale(_instant(b["heartbeat_at"]), now)]
        if fresh:
            return ANOTHER_BUILD_RUNNING.format(build_id=fresh[0]["build_id"], heartbeat=fresh[0]["heartbeat_at"])
        for build in running:
            self.store.fail_build(build["build_id"], STALE_BUILD, now.isoformat())
        return None

    def start(self) -> None:
        stamp = self.stamp()
        row = self.store.start_build({
            "fiscal_year": self.fy, "profile_version": self.pv, "embed_model": self.model,
            "embed_dims": EMBED_DIMS, "synonyms_version": self.job.synonyms.fingerprint,
            "stock_list_version": self.job.stock_list_version, "status": "running",
            "started_at": stamp, "heartbeat_at": stamp,
        })
        self.state.build_id = row["build_id"]

    # ── 2. targets ───────────────────────────────────────────────────────────

    def targets(self) -> list[CompanyReport]:
        reports = self.job.dart.reports(self.fy, self.job.stocks, self.job.parser_versions)
        if self.job.codes:
            wanted = list(dict.fromkeys(self.job.codes))
            reports = [r for r in reports if r.stock_code in wanted]
            missing = [c for c in wanted if c not in {r.stock_code for r in reports}]
            if missing:
                print(NOT_TARGETS.format(codes=", ".join(missing)), file=self.err)
        if self.job.limit:
            reports = reports[:self.job.limit]
        versions = sorted({r.parser_version for r in reports}, key=lambda v: parse_version(v) or ())
        self.progress(eligible=len(reports), parser_version=",".join(versions) or None)
        return reports

    # ── 3. profiles ──────────────────────────────────────────────────────────

    def base_row(self, report: CompanyReport, assembled: Optional[logic.AssembledInput]) -> dict:
        return {
            "fiscal_year": self.fy, "profile_version": self.pv, "stock_code": report.stock_code,
            "corp_code": report.corp_code, "corp_name": report.corp_name, "rcept_no": report.rcept_no,
            "report_name": report.report_name, "fiscal_end": report.fiscal_end,
            "parser_version": report.parser_version,
            "source_sections": list(assembled.sections) if assembled else [],
            "source_chars": len(assembled.text) if assembled else 0,
            "input_truncated": assembled.truncated if assembled else False,
        }

    def failed_row(self, report, assembled, reason: str) -> dict:
        return {**self.base_row(report, assembled), "status": "failed", "fail_reason": reason[:REASON_MAX],
                "llm_model": self.cfg.profile_model}

    async def profile_one(self, report: CompanyReport) -> None:
        assembled = logic.assemble_input(self.job.dart.sections(report))
        segments: list[dict] = []
        if not assembled.text:
            row = self.failed_row(report, assembled, EMPTY_INPUT)
        else:
            try:
                # asyncio.timeout, not wait_for: the call stays in this worker's task, so an
                # interruption unwinds through it instead of escaping from a task of its own.
                async with asyncio.timeout(self.cfg.per_company_timeout_s):
                    result = await extract_profile(
                        assembled.text, client=self.job.client, model=self.cfg.profile_model,
                        escalation_model=self.cfg.escalation_model, synonyms=self.job.synonyms,
                        corp_name=report.corp_name)
            except TimeoutError:
                row = self.failed_row(report, assembled, TIMEOUT.format(seconds=self.cfg.per_company_timeout_s))
            except Exception as exc:
                logger.warning("profile of %s failed: %s", report.stock_code, type(exc).__name__)
                row = self.failed_row(report, assembled, AI_ERROR.format(error=describe(exc)))
            else:
                row, segments = self.ok_rows(report, assembled, result)
        self.store.replace_profile(row, segments)
        if row["status"] == "ok":
            self.tally.profiled += 1
        else:
            self.tally.failed += 1
        self.progress(profiled=self.tally.profiled, failed=self.tally.failed)

    def ok_rows(self, report, assembled, result) -> tuple[dict, list[dict]]:
        profile = result.profile
        data = profile.model_dump()
        self.tally.input_tokens += result.tokens[0]
        self.tally.output_tokens += result.tokens[1]
        row = {
            **self.base_row(report, assembled), "status": "ok", "fail_reason": None, "profile": data,
            "one_line": profile.niche_industry or profile.summary, "is_holding": profile.is_holding,
            "is_financial": profile.is_financial, "info_quality": profile.info_quality,
            "terms": logic.profile_terms(data, self.job.synonyms), "llm_model": result.model,
            "input_tokens": result.tokens[0], "output_tokens": result.tokens[1],
            "grounding_ratio": round(result.grounding_ratio, 4),
        }
        segments = [{"fiscal_year": self.fy, "profile_version": self.pv, "stock_code": report.stock_code,
                     "seg_no": number, "name": segment.name, "products": segment.products,
                     "keywords": segment.keywords, "revenue_share_pct": segment.revenue_share_pct}
                    for number, segment in enumerate(profile.segments)]
        return row, segments

    async def profiles(self, targets: list[CompanyReport]) -> None:
        existing = {r["stock_code"]: r for r in self.store.profiles(
            self.fy, self.pv, "stock_code, status, rcept_no, parser_version")}
        todo = []
        for report in targets:
            old = existing.get(report.stock_code)
            if (old and old["status"] == "ok" and old["rcept_no"] == report.rcept_no
                    and old["parser_version"] == report.parser_version):
                self.tally.reused += 1
            else:
                todo.append(report)
        self.tally.profiled = self.tally.reused
        self.tally.extracted = len(todo)
        await _run_workers(todo, self.cfg.max_concurrent_llm, self.profile_one)

    # ── 4. embeddings ────────────────────────────────────────────────────────

    async def embed_once(self, texts: list[str]):
        async with asyncio.timeout(EMBED_TIMEOUT_S):
            return await self.job.client.embed(model=self.model, texts=texts, dimensions=EMBED_DIMS)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Unit vectors for ``texts`` (one call, retried once after a transient error)."""
        result = await call_with_retry(lambda: self.embed_once(texts))
        if len(result.vectors) != len(texts):
            raise RuntimeError(f"{len(texts)}개를 보냈는데 임베딩이 {len(result.vectors)}개 왔습니다")
        for vector in result.vectors:
            if len(vector) != EMBED_DIMS:
                raise RuntimeError(f"임베딩이 {len(vector)}차원입니다({EMBED_DIMS}차원이어야 함)")
        self.tally.embedding_tokens += result.input_tokens
        return [logic.normalize(v) for v in result.vectors]

    async def embeddings(self) -> None:
        ok = self.store.profiles(self.fy, self.pv, "stock_code, profile", status="ok")
        niche = {p["stock_code"]: (p.get("profile") or {}).get("niche_industry", "") for p in ok}
        have = self.store.company_embedding_codes(self.fy, self.pv, self.model)
        companies = [(p["stock_code"], logic.company_text(p.get("profile") or {}))
                     for p in ok if p["stock_code"] not in have]
        have_segments = self.store.segment_embedding_keys(self.fy, self.pv, self.model)
        segments = [((s["stock_code"], s["seg_no"]), logic.segment_text(niche[s["stock_code"]], s))
                    for s in self.store.segments(self.fy, self.pv)
                    if s["stock_code"] in niche and (s["stock_code"], s["seg_no"]) not in have_segments]
        for start in range(0, len(companies), EMBED_BATCH):
            batch = companies[start:start + EMBED_BATCH]
            vectors = await self.embed([text for _, text in batch])
            self.store.save_company_embeddings(self.fy, self.pv, self.model,
                                               [(code, v) for (code, _), v in zip(batch, vectors)])
            self.tally.embedded["companies"] += len(batch)
            self.progress()
        for start in range(0, len(segments), EMBED_BATCH):
            batch = segments[start:start + EMBED_BATCH]
            vectors = await self.embed([text for _, text in batch])
            self.store.save_segment_embeddings(self.fy, self.pv, self.model,
                                               [(code, no, v) for ((code, no), _), v in zip(batch, vectors)])
            self.tally.embedded["segments"] += len(batch)
            self.progress()

    # ── 5. publication, pilot table ──────────────────────────────────────────

    def vectors(self, ok_codes: set[str]):
        company = [(c, v) for c, v in self.store.company_vectors(self.fy, self.pv, self.model) if c in ok_codes]
        segment = [(c, n, v) for c, n, v in self.store.segment_vectors(self.fy, self.pv, self.model)
                   if c in ok_codes]
        return company, segment

    def publication(self) -> dict:
        """Rewrite ``terms`` of every ok profile; the term table and both percentile tables."""
        ok = self.store.profiles(self.fy, self.pv, "stock_code, profile, terms", status="ok")
        for row in ok:
            terms = logic.profile_terms(row.get("profile") or {}, self.job.synonyms)
            if terms != row.get("terms"):
                self.store.set_terms(self.fy, self.pv, row["stock_code"], terms)
        self.progress()
        company, segment = self.vectors({row["stock_code"] for row in ok})
        return {
            "term_table": logic.term_table([row.get("profile") or {} for row in ok], self.job.synonyms),
            "company_quantiles": logic.quantile_table([v for _, v in company]),
            "segment_quantiles": logic.quantile_table([v for _, _, v in segment],
                                                      [c for c, _, _ in segment]),
        }

    def name_of(self, code: str, profiles: dict) -> str:
        entry = self.job.stocks.lookup(code)
        return entry.name if entry else (profiles.get(code) or {}).get("corp_name") or ""

    def print_neighbours(self, targets: list[CompanyReport]) -> None:
        """The pilot's table: each target's ten most similar companies and segments."""
        rows = self.store.profiles(self.fy, self.pv, "stock_code, corp_name, profile", status="ok")
        profiles = {r["stock_code"]: r for r in rows}
        company, segment = self.vectors(set(profiles))
        company_codes = [c for c, _ in company]
        company_vectors = {c: v for c, v in company}
        segment_codes = [c for c, _, _ in segment]
        segment_nos = [n for _, n, _ in segment]
        segment_vectors = {(c, n): v for c, n, v in segment}
        names = {(c, n): s.get("name", "") for c, p in profiles.items()
                 for n, s in enumerate((p.get("profile") or {}).get("segments") or [])}
        for report in targets:
            code = report.stock_code
            profile = (profiles.get(code) or {}).get("profile") or {}
            print(f"■ {code} {self.name_of(code, profiles)} — {profile.get('niche_industry', '(프로필 없음)')}",
                  file=self.out)
            if code not in company_vectors:
                print("  프로필이나 임베딩이 없어 이웃을 계산하지 않았습니다.", file=self.out)
                continue
            print(f"  회사 유사도 상위 {NEIGHBOURS}", file=self.out)
            for rank, (other, similarity) in enumerate(
                    logic.top_companies(code, company_vectors[code], company_codes,
                                        [company_vectors[c] for c in company_codes], NEIGHBOURS), 1):
                print(f"  {rank:>3}. {other} {self.name_of(other, profiles)}  {similarity:.4f}", file=self.out)
            seed_segments = profile.get("segments") or []
            order = logic.segment_order([s.get("revenue_share_pct", -1) for s in seed_segments])
            seed = next((n for n in order if (code, n) in segment_vectors), None)
            if seed is None:
                print(f"  부문 유사도 상위 {NEIGHBOURS}: 부문이 없습니다.", file=self.out)
                continue
            share = logic.format_share(seed_segments[seed].get("revenue_share_pct"))
            print(f"  부문 유사도 상위 {NEIGHBOURS} (기준 부문: {seed_segments[seed].get('name', '')}"
                  f"{f' {share}%' if share else ''})", file=self.out)
            for rank, (other, number, similarity) in enumerate(
                    logic.top_segments(code, segment_vectors[(code, seed)], segment_codes, segment_nos,
                                       [segment_vectors[k] for k in zip(segment_codes, segment_nos)],
                                       NEIGHBOURS), 1):
                print(f"  {rank:>3}. {other} {self.name_of(other, profiles)}  {names.get((other, number), '')}"
                      f"  {similarity:.4f}", file=self.out)

    # ── 6. retention ─────────────────────────────────────────────────────────

    def retain(self) -> None:
        plan = logic.retention_plan(self.store.builds(), self.store.profile_keys(),
                                    self.store.embedding_keys(), self.pv)
        self.store.delete_builds(plan.delete_build_ids)
        for fiscal_year, profile_version in plan.delete_profile_keys:
            self.store.delete_profiles(fiscal_year, profile_version)
        for fiscal_year, profile_version, model in plan.delete_embedding_keys:
            self.store.delete_embeddings(fiscal_year, profile_version, model)

    # ── the whole run ────────────────────────────────────────────────────────

    def summary(self, status: str, eligible: int) -> dict:
        t = self.tally
        return {"build_id": self.state.build_id, "status": status, "fiscal_year": self.fy,
                "profile_version": self.pv, "embed_model": self.model, "eligible": eligible,
                "profiled": t.profiled, "failed": t.failed, "reused": t.reused, "extracted": t.extracted,
                "embedded": dict(t.embedded),
                "tokens": {"input": t.input_tokens, "output": t.output_tokens,
                           "embedding": t.embedding_tokens}}

    async def run(self) -> BuildOutcome:
        reason = self.refusal()
        if reason:
            print(reason, file=self.err)
            return BuildOutcome("refused", EXIT_CODES["refused"])
        self.start()
        targets = self.targets()
        if not targets:
            self.finish("failed", eligible=0, profiled=0, failed=0, message=NO_TARGETS)
            print(NO_TARGETS, file=self.err)
            summary = self.summary("failed", 0)
            print(json.dumps(summary, ensure_ascii=False, indent=2), file=self.out)
            return BuildOutcome("failed", EXIT_CODES["failed"], self.state.build_id, summary)
        await self.profiles(targets)
        await self.embeddings()
        status = logic.build_status(len(targets), self.tally.profiled, pilot=self.job.pilot)
        fields: dict[str, Any] = {"eligible": len(targets), "profiled": self.tally.profiled,
                                  "failed": self.tally.failed, "message": None}
        if status == "done":
            fields.update(self.publication())
        elif status == "pilot":
            self.print_neighbours(targets)
        else:
            fields["message"] = INCOMPLETE.format(profiled=self.tally.profiled, eligible=len(targets))
        self.finish(status, **fields)
        exit_code = EXIT_CODES[status]
        try:
            self.retain()
        except Exception as exc:
            print(LINE_CLEANUP_FAILED.format(error=describe(exc)), file=self.err)
            exit_code = 1
        summary = self.summary(status, len(targets))
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=self.out)
        return BuildOutcome(status, exit_code, self.state.build_id, summary)


def _record_failure(job: Job, state: BuildState, exc: BaseException) -> None:
    """Close the build as ``failed`` unless it was closed already (a failure to do so is only
    logged: the six-hour rule cleans it up)."""
    if state.build_id is None or state.finalized:
        return
    interrupted = isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError))
    message = INTERRUPTED if interrupted else ERROR.format(error=describe(exc))
    try:
        job.store.fail_build(state.build_id, message, job.now().isoformat())
        state.status = "failed"
    except Exception as failure:
        logger.warning("could not close build %s as failed: %s", state.build_id, type(failure).__name__)


async def run_build(job: Job, state: Optional[BuildState] = None) -> BuildOutcome:
    """Run one build (see the module docstring); errors and cancellation close it as failed
    and propagate."""
    state = state if state is not None else BuildState()
    try:
        return await _Build(job, state).run()
    except BaseException as exc:
        _record_failure(job, state, exc)
        raise
    finally:
        await job.client.close()


def execute(job: Job) -> int:
    """``run_build`` in its own event loop → the command's exit code. An error or Ctrl+C closes
    the build as failed, explains it on stderr and gives 1."""
    state = BuildState()
    err = job.err or sys.stderr
    try:
        outcome = asyncio.run(run_build(job, state))
    except (KeyboardInterrupt, asyncio.CancelledError) as exc:
        _record_failure(job, state, exc)
        print(LINE_INTERRUPTED.format(note=state.note()), file=err)
        return 1
    except Exception as exc:
        _record_failure(job, state, exc)
        traceback.print_exc(file=err)
        print(LINE_FAILED.format(error=describe(exc), note=state.note()), file=err)
        return 1
    return outcome.exit_code
