"""Commands ``peers build`` and ``peers inspect`` (spec §11).

``register(subparsers)`` adds them; each sets ``func(args) -> exit code``. Importing this module
loads nothing heavy: the DB, MongoDB and AI clients, the build and the reports window load inside
the command functions (``python -m research_desk`` imports every window when it starts).

``peers build [--fiscal-year N] [--codes …] [--limit n] [--pilot]``
- Start-up checks, in this order, before anything is done (exit 4, one line on stderr): DB
  settings (SUPABASE_URL, SUPABASE_SERVICE_KEY); the keys of the profile model and the escalation
  model (or the codex CLI for a ``codex:`` model); OPENAI_API_KEY (embeddings); a MongoDB
  ``ping``; the stock list, which must load and match its version file (the rule of ``tag``);
  the synonym table; at least one business-report document of the fiscal year with
  ``parser_version`` 0.2.0 or later.
- Then, while the tagger is working (``reports.tagging_in_progress()``), nothing starts:
  ``분류 작업이 진행 중이라 유사도 계산을 시작하지 않았습니다. 분류가 끝난 뒤 다시 실행하세요.``,
  exit 1. Loading the reports window loads FastAPI: this command needs the web packages.
- Then ``build.execute``: another running build → 1; done 0, incomplete 1, pilot 0, an error or
  Ctrl+C → the build closes as failed, 1. A malformed number setting raises (exit 1).

``peers inspect``: profile counts by fiscal year, profile version and status with their tokens,
the token totals and the latest builds, as JSON. Without DB settings, 4.

Both commands first set stdout and stderr to write ``?`` for a character their encoding lacks
instead of failing: piped or redirected output on Korean Windows is cp949, which has no em dash,
en dash or ``é``, and AI-written text and error messages can hold them. The encoding stays.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from research_desk.core import settings as core_settings

EXIT_NOT_READY = 4
EXIT_FAILED = 1

DB_NOT_CONFIGURED = "DB 접속 설정(SUPABASE_URL, SUPABASE_SERVICE_KEY)이 없습니다"
KEY_MISSING = "{env}가 설정되지 않았습니다"
CODEX_MISSING = "codex CLI를 찾을 수 없습니다(모델 {model}). 설치한 뒤 codex login으로 로그인하세요"
MONGO_UNREACHABLE = ("사업보고서 DB(MongoDB)에 접속하지 못했습니다. MongoDB가 켜져 있는지와 "
                     "DART_MONGO_URL을 확인하세요")
STOCK_LIST_UNREADABLE = "종목표 파일을 읽을 수 없습니다: {reason}"
# The same sentence as the tagger's (one cause, one sentence).
STOCK_LIST_VERSION_MISMATCH = (
    "종목표 파일 내용이 버전 정보와 다릅니다. 종목표를 바꿨다면 "
    "python -m research_desk stocks set-version --as-of <자료 기준일, YYYY-MM-DD>를 "
    "실행한 뒤 다시 시작하세요."
)
SYNONYMS_UNREADABLE = "동의어표(features/peers/synonyms.yaml)를 읽을 수 없습니다: {reason}"
NO_REPORT_TEXTS = ("{year} 회계연도 사업보고서 중 parser_version 0.2.0 이상인 문서가 없습니다. "
                   "DART 수집을 먼저 하세요")
TAGGING_IN_PROGRESS = "분류 작업이 진행 중이라 유사도 계산을 시작하지 않았습니다. 분류가 끝난 뒤 다시 실행하세요."

INSPECT_BUILDS = 10
_CODE = re.compile(r"^[0-9A-Z]{6}$")


class NotReadyToRun(Exception):
    """A start-up check failed; ``str()`` is the stderr line (exit code 4)."""


# ── arguments ────────────────────────────────────────────────────────────────

def parse_codes(text: str) -> list[str]:
    """``--codes``: stock codes separated by commas or spaces, upper-cased, each once."""
    codes = [code.upper() for code in re.split(r"[\s,]+", text or "") if code]
    if not codes:
        raise argparse.ArgumentTypeError("종목코드를 하나 이상 적으세요 (예: 005930,080220)")
    bad = [code for code in codes if not _CODE.match(code)]
    if bad:
        raise argparse.ArgumentTypeError(f"종목코드는 영문 대문자·숫자 6자리입니다: {', '.join(bad)}")
    return list(dict.fromkeys(codes))


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"1 이상의 정수여야 합니다: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 이상의 정수여야 합니다: {text!r}")
    return value


# ── connections (tests replace these) ────────────────────────────────────────

def _supabase(url: str, key: str) -> Any:
    from research_desk.core import db

    return db.supabase_client(url, key)


def _mongo_ping(url: str) -> bool:
    from research_desk.core import mongo

    return mongo.ping(url)


def _mongo_collection(url: str, db_name: str, name: str) -> Any:
    from research_desk.core import mongo

    return mongo.mongo_collection(url, db_name, name)


def _llm_client(timeout_s: float) -> Any:
    from research_desk.core import llm

    return llm.LLMClient(openai_api_key=core_settings.openai_api_key(),
                         anthropic_api_key=core_settings.anthropic_api_key(), timeout=timeout_s)


# ── start-up checks ──────────────────────────────────────────────────────────

def _db_settings() -> tuple[str, str]:
    url, key = core_settings.supabase_url(), core_settings.supabase_service_key()
    if not (url and key):
        raise NotReadyToRun(DB_NOT_CONFIGURED)
    return url, key


def _check_model(model: str) -> None:
    """The API key the model's provider needs; for a ``codex:`` model, the codex CLI."""
    from research_desk.core import llm

    env = llm.api_key_env(model)
    if env is None:
        try:
            llm.LLMClient().require_key(model)
        except RuntimeError:
            raise NotReadyToRun(CODEX_MISSING.format(model=model)) from None
    elif not core_settings.optional(env):
        raise NotReadyToRun(KEY_MISSING.format(env=env))


def _stock_list():
    """The stock list and its version; it must load and match its version file."""
    from research_desk.domain.stocks import StockList, StockListError

    try:
        stocks = StockList.load(core_settings.krx_csv_path())
    except StockListError as exc:
        raise NotReadyToRun(STOCK_LIST_UNREADABLE.format(reason=exc)) from exc
    check = stocks.verify()
    if not check.ok:
        raise NotReadyToRun(STOCK_LIST_VERSION_MISMATCH)
    return stocks, check.version


def _synonyms():
    from . import settings

    try:
        return settings.synonyms()
    except (OSError, ValueError) as exc:
        raise NotReadyToRun(SYNONYMS_UNREADABLE.format(reason=exc)) from exc


def _not_ready(exc: Exception) -> int:
    print(exc, file=sys.stderr)
    return EXIT_NOT_READY


def _tolerant(stream: Any) -> Any:
    """``stream`` set to write ``?`` for a character its encoding lacks (same object, same
    encoding); a stream without ``reconfigure`` (a ``StringIO``) is left as it is."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="replace")
    return stream


# ── peers build ──────────────────────────────────────────────────────────────

def build_command(args) -> int:
    out, err = _tolerant(sys.stdout), _tolerant(sys.stderr)
    core_settings.load_env()
    from . import settings

    cfg = settings.load_settings()
    fiscal_year = args.fiscal_year if args.fiscal_year is not None else cfg.fiscal_year
    try:
        url, key = _db_settings()
        _check_model(cfg.profile_model)
        _check_model(cfg.escalation_model)
        if not core_settings.openai_api_key():
            raise NotReadyToRun(KEY_MISSING.format(env="OPENAI_API_KEY"))
        if not _mongo_ping(cfg.mongo_url):
            raise NotReadyToRun(MONGO_UNREACHABLE)
        stocks, stock_list_version = _stock_list()
        synonyms = _synonyms()
    except NotReadyToRun as exc:
        return _not_ready(exc)

    from .dart import DartSource

    dart = DartSource(_mongo_collection(cfg.mongo_url, cfg.mongo_db, cfg.mongo_collection))
    try:
        parser_versions = dart.parser_versions(fiscal_year)
        if not parser_versions:
            return _not_ready(NotReadyToRun(NO_REPORT_TEXTS.format(year=fiscal_year)))
        # The reports window owns the reports table; loading it loads FastAPI.
        from research_desk.features import reports

        try:
            tagging = reports.tagging_in_progress()
        except core_settings.NotReady as exc:   # passed on as it is: the reports feature's own words
            return _not_ready(exc)
        if tagging:
            print(TAGGING_IN_PROGRESS, file=err)
            return EXIT_FAILED

        from . import build
        from .store import PeersStore

        job = build.Job(store=PeersStore(_supabase(url, key)), dart=dart,
                        client=_llm_client(cfg.per_company_timeout_s), stocks=stocks,
                        stock_list_version=stock_list_version, parser_versions=parser_versions,
                        synonyms=synonyms, cfg=cfg, fiscal_year=fiscal_year, codes=args.codes,
                        limit=args.limit, pilot=args.pilot, out=out, err=err)
        return build.execute(job)
    finally:
        dart.close()


# ── peers inspect ────────────────────────────────────────────────────────────

def inspect_report(store) -> dict:
    """Profile counts and tokens by (fiscal year, profile version, status), token totals, and the
    latest builds without their tables."""
    groups: dict[tuple, dict] = {}
    for row in store.all_profiles("fiscal_year, profile_version, status, input_tokens, output_tokens"):
        key = (row["fiscal_year"], row["profile_version"], row["status"])
        group = groups.setdefault(key, {"fiscal_year": key[0], "profile_version": key[1], "status": key[2],
                                        "count": 0, "input_tokens": 0, "output_tokens": 0})
        group["count"] += 1
        group["input_tokens"] += row.get("input_tokens") or 0
        group["output_tokens"] += row.get("output_tokens") or 0
    profiles = [groups[key] for key in sorted(groups, key=lambda k: (k[0], k[1], k[2] or ""))]
    return {
        "profiles": profiles,
        "tokens": {"input": sum(g["input_tokens"] for g in profiles),
                   "output": sum(g["output_tokens"] for g in profiles)},
        "builds": store.recent_builds(INSPECT_BUILDS),
    }


def inspect_command(args) -> int:
    out = _tolerant(sys.stdout)
    _tolerant(sys.stderr)
    core_settings.load_env()
    try:
        url, key = _db_settings()
    except NotReadyToRun as exc:
        return _not_ready(exc)
    from .store import PeersStore

    report = inspect_report(PeersStore(_supabase(url, key)))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str), file=out)
    return 0


# ── command list entry ───────────────────────────────────────────────────────

def register(subparsers) -> None:
    """Add ``peers`` with ``build`` and ``inspect``; each sets ``func(args) -> exit code``."""
    peers = subparsers.add_parser("peers", help="유사 기업: build / inspect")
    sub = peers.add_subparsers(dest="peers_command", required=True)

    p_build = sub.add_parser(
        "build", help="사업보고서로 회사 프로필·임베딩을 만들고 유사도 계산 결과를 공개한다(연 1회)")
    p_build.add_argument("--fiscal-year", type=int, default=None,
                         help="회계연도 (기본: PEERS_FISCAL_YEAR, 2025)")
    p_build.add_argument("--codes", type=parse_codes, default=None,
                         help="이 종목들만 대상으로 (시험용, 쉼표로 구분: 005930,080220)")
    p_build.add_argument("--limit", type=_positive, default=None,
                         help="대상 중 종목코드 순서로 앞의 n개만 (시험용)")
    p_build.add_argument("--pilot", action="store_true",
                         help="시험 실행: 화면에 내놓지 않고 회사마다 비슷한 회사 상위 10을 출력")
    p_build.set_defaults(func=build_command)

    p_inspect = sub.add_parser("inspect", help="프로필 수·토큰 합계·최근 빌드를 JSON으로 출력")
    p_inspect.set_defaults(func=inspect_command)
