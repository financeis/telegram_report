import React, { useEffect, useState } from "react";
import { LoaderCircle, Search, SearchX, Telescope } from "lucide-react";
import { api } from "../api";
import { PeriodHeader, ReportsCell, ReturnsCell } from "./PeerTable";
import {
  DEFAULT_WINDOW,
  WINDOWS,
  basisText,
  count,
  dotDate,
  eok,
  share,
} from "./labels";
import "./peers.css";

// POST /api/peers/search takes 2–100 characters once the outer spaces are dropped; a shorter
// query is stopped here, since the 422 for it carries no sentence to show.
const MIN_LENGTH = 2;
const MAX_LENGTH = 100;
const LIMIT = 30;
const fromAddress = () => new URLSearchParams(location.search).get("q") || "";
const searchable = (q) => q.length >= MIN_LENGTH && q.length <= MAX_LENGTH;

// 테마로 기업 찾기 (?view=themes&q=…): a search box, the window and the result table. A result
// opens that company's 유사 기업 tab.
export default function ThemeSearch({ onStock }) {
  const [text, setText] = useState(fromAddress),
    [query, setQuery] = useState(() => {
      const q = fromAddress().trim();
      return searchable(q) ? q : "";
    }),
    [period, setPeriod] = useState(DEFAULT_WINDOW),
    [hint, setHint] = useState(""),
    [data, setData] = useState(null),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const url = new URL(location.href);
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    history.replaceState({}, "", url);
    if (!query) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setData(null);
    // Every result carries all three windows' returns, so changing the window only relabels the
    // table; the request names the window shown when the search started.
    api("/peers/search", {
      method: "POST",
      body: JSON.stringify({ q: query, window: period, limit: LIMIT }),
      signal: controller.signal,
    })
      .then((x) => {
        setData(x);
        setLoading(false);
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => controller.abort();
  }, [query, retry]);
  function submit(e) {
    e.preventDefault();
    const q = text.trim();
    if (!searchable(q)) {
      setHint("두 글자 이상 입력해 주세요.");
      return;
    }
    setHint("");
    setQuery(q);
    setRetry((x) => x + 1);
  }
  function open(next) {
    onStock(next);
    window.scrollTo({ top: 0 });
  }
  const results = data?.results || [];
  return (
    <section className="themes-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">THEME DISCOVERY</span>
          <h1>테마로 기업 찾기</h1>
          <p className="page-description">
            제품·기술·테마 이름으로 사업이 맞닿은 상장사를 찾습니다. 결과를
            누르면 그 회사를 기준으로 유사 기업을 엽니다.
          </p>
        </div>
      </div>
      <form className="themes-toolbar" role="search" onSubmit={submit}>
        <label className="themes-input">
          <Search size={16} />
          <input
            aria-label="테마 검색어"
            placeholder="예: 레거시 DRAM, 전력 반도체, 수소 연료전지"
            value={text}
            maxLength={MAX_LENGTH}
            onChange={(e) => {
              setText(e.target.value);
              setHint("");
            }}
          />
        </label>
        <button type="submit" className="button primary" disabled={loading}>
          {loading ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <Search size={14} />
          )}
          찾기
        </button>
        <div className="segmented" role="group" aria-label="수익률 기간">
          {WINDOWS.map(([value, name]) => (
            <button
              key={value}
              type="button"
              className={period === value ? "active" : ""}
              aria-pressed={period === value}
              onClick={() => setPeriod(value)}
            >
              {name}
            </button>
          ))}
        </div>
      </form>
      {hint && (
        <p className="themes-hint" role="alert">
          {hint}
        </p>
      )}
      {!query ? (
        <div className="panel peers-empty">
          <Telescope size={28} />
          <h3>찾고 싶은 테마를 입력하세요</h3>
          <p>
            사업보고서로 만든 회사별 사업 요약과 견주어, 가까운 회사부터 보여
            줍니다.
          </p>
        </div>
      ) : loading ? (
        <div className="comparison-loading" role="status">
          <LoaderCircle className="spin" size={17} />‘{query}’ 관련 회사를 찾고
          있습니다.
        </div>
      ) : error ? (
        <div className="error-panel" role="alert">
          <p>{error}</p>
          <button
            type="button"
            className="button secondary"
            onClick={() => setRetry((x) => x + 1)}
          >
            다시 시도
          </button>
        </div>
      ) : data ? (
        <>
          <p className="themes-summary">
            <strong>‘{data.query ?? query}’</strong> 검색 결과{" "}
            {count(results.length)}곳 · 기준 {basisText(data.basis)} · 주가
            기준일 {data.price_as_of ? dotDate(data.price_as_of) : "자료 없음"}
          </p>
          <div className="panel peers-panel">
            {results.length ? (
              <div className="peer-table-wrap">
                <table className="peer-table theme-table">
                  <thead>
                    <tr>
                      <th>회사</th>
                      <th>일치 용어</th>
                      <th>겹치는 부문 · 비중</th>
                      <th>리포트 · 최근 1년</th>
                      <PeriodHeader period={period} />
                      <th className="num">시총</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((r) => (
                      <tr
                        key={r.code}
                        className="peer-row"
                        onClick={() => open(r.code)}
                      >
                        <td className="peer-company">
                          <button
                            type="button"
                            className="peer-name"
                            onClick={(e) => {
                              e.stopPropagation();
                              open(r.code);
                            }}
                          >
                            {r.name || r.code}
                          </button>
                          <small>
                            {r.code} · {r.market || "시장 미상"}
                            {r.sector_minor ? ` · ${r.sector_minor}` : ""}
                          </small>
                          {r.one_line && (
                            <p className="peer-one-line">{r.one_line}</p>
                          )}
                        </td>
                        <td>
                          {r.matched_terms?.length ? (
                            <div className="peer-terms">
                              {r.matched_terms.map((t, i) => (
                                <span key={`${t}-${i}`}>{t}</span>
                              ))}
                            </div>
                          ) : (
                            <span className="peer-none">직접 일치 없음</span>
                          )}
                        </td>
                        <td>
                          {r.segment_match ? (
                            <>
                              <span>
                                {r.segment_match.segment || "이름 없는 부문"}
                              </span>
                              <small className="peer-sub">
                                {share(r.segment_match.revenue_share_pct)}
                              </small>
                            </>
                          ) : (
                            <span className="peer-none">—</span>
                          )}
                        </td>
                        <ReportsCell coverage={r.coverage} />
                        <ReturnsCell price={r.price} period={period} />
                        <td className="num">{eok(r.price?.market_cap)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="peers-empty">
                <SearchX size={26} />
                <h3>검색어와 맞는 회사를 찾지 못했습니다</h3>
                <p>다른 표현이나 더 넓은 용어로 찾아보세요.</p>
              </div>
            )}
          </div>
        </>
      ) : null}
    </section>
  );
}
