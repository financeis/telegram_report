"""KIS (한국투자증권) Open API: adjusted daily prices and quotes of Korean stocks.

``KisClient`` is synchronous. httpx is imported inside the methods, never at
the top of this module: every command imports core when it starts, and only
work that calls KIS should load an HTTP library.

- Access token: asked for once a client, by ``ensure_token()`` or else by the
  first call, and reused for the client's life (KIS issues about one token a
  minute and the app key is shared with another project; a token lasts a day).
  Make one client a run. A failed token request is final: from then on every
  call of that client raises ``KisError`` at once without asking KIS again (the
  retries below happen inside that one request).
- Pace: call starts are at least ``1 / max_calls_per_sec`` apart, the token
  call included, also across threads that share the client.
- Retries: a rate-limit answer (HTTP 429 or KIS ``EGW00201``), a 5xx answer, a
  timeout or a dropped connection is tried again up to ``MAX_RETRIES`` times,
  waiting 1, 2 and 4 seconds. Any other failure raises ``KisError`` at once.
- Secrets: the app key, app secret and token go only into request headers and
  the token request body. ``KisError`` text and log lines never hold them; an
  answer that repeats one shows ``***`` instead.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.koreainvestment.com:9443"  # 실전 서비스
TOKEN_PATH = "/oauth2/tokenP"

# 국내주식기간별시세(일/주/월/년) [v1_국내주식-016]: at most 100 rows a call, newest first.
DAILY_PATH = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
DAILY_TR_ID = "FHKST03010100"
DAILY_PAGE_ROWS = 100

# 주식현재가 시세 [v1_국내주식-008]
QUOTE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-price"
QUOTE_TR_ID = "FHKST01010100"
MARKET_CAP_UNIT = 100_000_000  # hts_avls (HTS 시가총액) is in 억원
HALTED_STATUS = "58"  # iscd_stat_cls_code 58: 거래정지
ADMIN_ISSUE_STATUS = "51"  # iscd_stat_cls_code 51: 관리종목

RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수를 초과하였습니다.
MAX_RETRIES = 3
BACKOFF_S = 1.0  # doubles every retry: 1, 2, 4 s


class KisError(RuntimeError):
    """A KIS call failed. Its text holds no app key, app secret or token.

    ``code``: KIS's message code when the answer had one (e.g. ``EGW00201``).
    ``status``: the HTTP status when there was an answer.
    """

    def __init__(self, message: str, *, code: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status = status


class KisClient:
    """KIS Open API client for one run.

    ``app_key``/``app_secret``: the 실전 account's. ``max_calls_per_sec``: the
    most call starts in a second. ``timeout_s``: one HTTP request's limit (a
    timeout is tried again like a 5xx). ``sleep``, ``clock`` and ``transport``
    (an httpx transport) are for tests. Close it with ``close()`` or use it as
    a context manager.
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        *,
        max_calls_per_sec: float,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        transport: Any = None,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("KIS needs an app key and an app secret")
        if not max_calls_per_sec > 0:
            raise ValueError(f"max_calls_per_sec must be above 0, got {max_calls_per_sec}")
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._interval = 1.0 / max_calls_per_sec
        self._sleep = sleep
        self._clock = clock
        self._transport = transport
        self._http: Any = None
        self._token: Optional[str] = None
        # (message, code, status) of the failed token request: the client does not ask again
        self._token_failure: Optional[tuple[str, Optional[str], Optional[int]]] = None
        self._token_lock = threading.Lock()
        self._pace_lock = threading.Lock()
        self._next_start = float("-inf")

    # ── calls ────────────────────────────────────────────────────────────────

    def ensure_token(self) -> None:
        """Get the access token now, before any data call (a run asks for it once).

        Nothing happens when the client has it already. ``KisError`` when it cannot be
        had; the client then never asks KIS for a token again (see ``_access_token``).
        """
        self._access_token()

    def daily_prices(self, code: str, start: date, end: date) -> list[dict]:
        """Adjusted (수정주가) daily rows of ``code`` from ``start`` to ``end``, oldest first.

        A row is ``{"date": date, "close": int, "volume": int, "trading_value": int}``
        (trading_value: 거래대금 in won; a blank figure is None). KIS answers at
        most DAILY_PAGE_ROWS rows a call, newest first, so a longer span is read
        backwards in several calls and merged.
        """
        rows: dict[date, dict] = {}
        upper = end
        while start <= upper:
            body = self._get(DAILY_PATH, DAILY_TR_ID, {
                "FID_COND_MRKT_DIV_CODE": "J",  # KRX
                "FID_INPUT_ISCD": code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": upper.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",  # daily
                "FID_ORG_ADJ_PRC": "0",  # 0: 수정주가 (adjusted), 1: 원주가
            }, what=f"daily prices {code}")
            page = [_daily_row(raw) for raw in body.get("output2") or []
                    if str(raw.get("stck_bsop_date") or "").strip()]
            rows.update((row["date"], row) for row in page if start <= row["date"] <= end)
            if len(page) < DAILY_PAGE_ROWS:
                break
            oldest = min(row["date"] for row in page)
            if oldest > upper:  # an answer past the asked span: stop rather than ask again
                break
            upper = oldest - timedelta(days=1)
        return [rows[day] for day in sorted(rows)]

    def quote(self, code: str) -> dict:
        """Market cap, listed shares and trading status of ``code`` now.

        ``{"market_cap": int | None, "listed_shares": int | None, "halted": bool,
        "admin_issue": bool}``. market_cap is in won (KIS gives 억원). halted:
        임시정지 (temp_stop_yn) or status 거래정지. admin_issue: 관리종목
        (mang_issu_cls_code) or status 관리종목.
        """
        body = self._get(QUOTE_PATH, QUOTE_TR_ID, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                         what=f"quote {code}")
        fields = body.get("output") or {}
        market_cap = _int(fields.get("hts_avls"))
        status = str(fields.get("iscd_stat_cls_code") or "").strip()
        return {
            "market_cap": None if market_cap is None else market_cap * MARKET_CAP_UNIT,
            "listed_shares": _int(fields.get("lstn_stcn")),
            "halted": _yes(fields.get("temp_stop_yn")) or status == HALTED_STATUS,
            "admin_issue": _yes(fields.get("mang_issu_cls_code")) or status == ADMIN_ISSUE_STATUS,
        }

    def close(self) -> None:
        """Close the HTTP connections."""
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "KisClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _get(self, path: str, tr_id: str, params: dict, *, what: str) -> dict:
        """One data call: the JSON body when KIS answers rt_cd 0, else KisError."""
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
            "tr_id": tr_id,
            "custtype": "P",  # 개인
        }
        status, body = self._call("GET", path, what=what, params=params, headers=headers)
        if body.get("rt_cd") != "0":
            problem = _kis_message(body) or "the answer has no rt_cd 0"
            raise KisError(self._mask(f"KIS {what} failed: {problem}"),
                           code=body.get("msg_cd") or None, status=status)
        return body

    def _access_token(self) -> str:
        """The client's token: asked for once, then reused.

        A failed token request is final for the client. That failure goes up as it came;
        every later call raises KisError at once (the same code and status, the first
        failure's text) without asking KIS again, also in threads that waited for it.
        """
        with self._token_lock:
            if self._token is not None:
                return self._token
            if self._token_failure is not None:
                message, code, status = self._token_failure
                raise KisError(f"{message} (the client's one token request failed earlier; "
                               "KIS is not asked again)", code=code, status=status)
            try:
                self._token = self._issue_token()
            except KisError as exc:
                self._token_failure = (str(exc), exc.code, exc.status)
                raise
            except Exception as exc:
                self._token_failure = (self._mask(f"KIS access token failed: {_describe(exc)}"), None, None)
                raise
            return self._token

    def _issue_token(self) -> str:
        """One token request, paced and retried like any call: the token, else KisError."""
        status, body = self._call("POST", TOKEN_PATH, what="access token", json={
            "grant_type": "client_credentials",
            "appkey": self._app_key,
            "appsecret": self._app_secret,
        })
        token = body.get("access_token")
        if not token:
            problem = _kis_message(body) or "the answer has no access_token"
            raise KisError(self._mask(f"KIS access token failed: {problem}"),
                           code=body.get("error_code") or None, status=status)
        return token

    def _call(self, method: str, path: str, *, what: str, **request: Any) -> tuple[int, dict]:
        """Send one request, paced and retried: (status, JSON body) of a 2xx answer.

        A rate limit (HTTP 429 or EGW00201), a 5xx, a timeout or a dropped
        connection is tried again up to MAX_RETRIES times with doubling waits;
        any other answer raises KisError at once.
        """
        import httpx

        http = self._connections()
        for attempt in range(MAX_RETRIES + 1):
            self._pace()
            status: Optional[int] = None
            code: Optional[str] = None
            try:
                response = http.request(method, path, **request)
            except httpx.TransportError as exc:
                problem = _describe(exc)
                # Timeouts and dropped connections pass; a URL httpx cannot use does not.
                retry = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError,
                                         httpx.RemoteProtocolError))
            else:
                status, body = response.status_code, _json_object(response)
                code = body.get("msg_cd") or body.get("error_code") or None
                if 200 <= status < 300 and code != RATE_LIMIT_CODE:
                    return status, body
                problem = " ".join(filter(None, [f"HTTP {status}", _kis_message(body)]))
                retry = status == 429 or status >= 500 or code == RATE_LIMIT_CODE
            if not retry or attempt == MAX_RETRIES:
                after = f" after {MAX_RETRIES} retries" if retry else ""
                raise KisError(self._mask(f"KIS {what} failed{after}: {problem}"), code=code, status=status)
            wait = BACKOFF_S * 2 ** attempt
            logger.warning("%s", self._mask(
                f"KIS {what}: {problem}; retry {attempt + 1}/{MAX_RETRIES} in {wait:g}s"))
            self._sleep(wait)
        raise AssertionError("unreachable: the last attempt returns or raises")

    def _pace(self) -> None:
        """Wait for this call's start: starts are at least 1/max_calls_per_sec apart."""
        with self._pace_lock:
            now = self._clock()
            start = max(now, self._next_start)
            self._next_start = start + self._interval
        if start > now:
            self._sleep(start - now)

    def _connections(self) -> Any:
        """The httpx client, made on first use."""
        import httpx

        if self._http is None:
            self._http = httpx.Client(base_url=self._base_url, timeout=self._timeout_s,
                                      transport=self._transport)
        return self._http

    def _mask(self, text: str) -> str:
        """``text`` with the app key, app secret and token replaced by ***."""
        for secret in (self._app_key, self._app_secret, self._token):
            if secret:
                text = text.replace(secret, "***")
        return text


def _daily_row(raw: dict) -> dict:
    return {
        "date": datetime.strptime(str(raw["stck_bsop_date"]).strip(), "%Y%m%d").date(),
        "close": _int(raw.get("stck_clpr")),
        "volume": _int(raw.get("acml_vol")),
        "trading_value": _int(raw.get("acml_tr_pbmn")),
    }


def _int(value: Any) -> Optional[int]:
    """KIS sends numbers as strings; a blank one is None."""
    text = str(value).strip() if value is not None else ""
    return int(Decimal(text)) if text else None


def _yes(value: Any) -> bool:
    return str(value or "").strip().upper() == "Y"


def _json_object(response: Any) -> dict:
    """The answer's JSON object, or {} when the body is not one (e.g. a gateway error page)."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _kis_message(body: dict) -> str:
    """KIS's code and text: msg_cd/msg1 on data calls, error_code/error_description on the token call."""
    parts = (body.get("msg_cd") or body.get("error_code"), body.get("msg1") or body.get("error_description"))
    return " ".join(str(part).strip() for part in parts if part)


def _describe(exc: Exception) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
