import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Building2,
  BarChart3,
  ClipboardCheck,
  Check,
  ChevronRight,
  Columns2,
  ExternalLink,
  FileText,
  Layers3,
  LoaderCircle,
  PanelRightClose,
  Plus,
  Search,
  SlidersHorizontal,
  Sparkles,
  Star,
  Telescope,
  X,
} from "lucide-react";
import "./theme.css";
import "./styles.css";
import "./migration.css";
import { api } from "./api";
import Coverage, { StockActivity } from "./Coverage";
import ReviewQueue from "./ReviewQueue";
import Peers from "./peers/Peers";
import ThemeSearch from "./peers/ThemeSearch";
import StatusLine from "./freshness/StatusLine";

const number = (value) =>
  value == null
    ? "—"
    : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: 2 });
const date = (value) =>
  value ? value.slice(0, 10).replaceAll("-", ".") : "날짜 미상";
const price = (value) => (value == null ? "미기재" : `${number(value)}원`);
const title = (r) => r.title || r.file_name || "제목 없는 보고서";
const details = (r) => r?.summary?.financial_details;
const rating = (r) =>
  details(r)?.rating?.current_label ||
  r?.summary?.recommendation_raw ||
  r?.summary?.recommendation ||
  "—";
const chronological = (a, b) =>
  (a.published_at || "").localeCompare(b.published_at || "") || a.id - b.id;
function stored(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
function remember(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {}
}
function Badge({ children, tone = "" }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}
function Empty({ title: heading, children }) {
  return (
    <div className="empty">
      <FileText size={30} />
      <h3>{heading}</h3>
      <p>{children}</p>
    </div>
  );
}
function Source({ evidence, report }) {
  if (!evidence) return null;
  return (
    <details className="source">
      <summary>원문 근거 · p.{evidence.page}</summary>
      <blockquote>{evidence.quote}</blockquote>
      <a
        href={`${report.pdf_url}#page=${evidence.page}`}
        target="_blank"
        rel="noreferrer"
      >
        PDF {evidence.page}페이지 열기 <ArrowUpRight size={12} />
      </a>
    </details>
  );
}
function Points({ heading, items, risk = false }) {
  return (
    <section className={`points ${risk ? "risk" : ""}`}>
      <h4>
        <span />
        {heading}
      </h4>
      {items?.length ? (
        <ul>
          {items.map((x, i) => (
            <li key={i}>{x}</li>
          ))}
        </ul>
      ) : (
        <p className="muted">추출된 내용이 없습니다.</p>
      )}
    </section>
  );
}
function App() {
  const initialCode =
    new URLSearchParams(location.search).get("stock") ||
    stored("research:lastStock", "016360");
  const [code, setCode] = useState(initialCode),
    [catalog, setCatalog] = useState([]),
    [favorites, setFavorites] = useState([]);
  const [recent, setRecent] = useState(stored("research:recent", []));
  const [data, setData] = useState(null),
    [loading, setLoading] = useState(true),
    [loadError, setLoadError] = useState("");
  const [catalogError, setCatalogError] = useState("");
  const [search, setSearch] = useState(""),
    [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState(""),
    [publisher, setPublisher] = useState(""),
    [status, setStatus] = useState(""),
    [period, setPeriod] = useState("all");
  const [selected, setSelected] = useState([]),
    [focused, setFocused] = useState(null),
    [view, setView] = useState(
      new URLSearchParams(location.search).get("view") || "reports",
    );
  const [stockMode, setStockMode] = useState("library"),
    [reportType, setReportType] = useState(""),
    [pageSize, setPageSize] = useState(20);
  const [analysisProgress, setAnalysisProgress] = useState(null);
  const cancelAnalysis = useRef(false),
    runningAnalysis = useRef(false);
  const [toast, setToast] = useState(""),
    [busy, setBusy] = useState(null),
    [reload, setReload] = useState(0);
  const searchRef = useRef(null);
  useEffect(() => {
    api("/workspace")
      .then((x) => {
        setCatalog(x.stocks);
        setFavorites(x.favorites);
      })
      .catch((e) => setCatalogError(e.message));
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError("");
    setData(null);
    setSelected([]);
    setFocused(null);
    setPublisher("");
    setReportType("");
    setPageSize(20);
    setQuery("");
    setStatus("");
    api(`/stocks/${encodeURIComponent(code)}/reports`, {
      signal: controller.signal,
    })
      .then((x) => {
        setData(x);
        setLoading(false);
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setLoadError(e.message);
          setLoading(false);
        }
      });
    remember("research:lastStock", code);
    const url = new URL(location.href);
    url.searchParams.set("stock", code);
    history.replaceState({}, "", url);
    setRecent((previous) => {
      const next = [code, ...previous.filter((c) => c !== code)].slice(0, 5);
      remember("research:recent", next);
      return next;
    });
    return () => controller.abort();
  }, [code, reload]);
  useEffect(() => {
    if (toast) {
      const timer = setTimeout(() => setToast(""), 5000);
      return () => clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    const handler = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        setSearchOpen(true);
      }
      if (e.key === "Escape") {
        setSearchOpen(false);
        setFocused(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);
  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.set("view", view);
    history.replaceState({}, "", url);
  }, [view]);
  useEffect(
    () => setPageSize(20),
    [query, publisher, status, period, reportType],
  );
  const stock = data?.stock || catalog.find((s) => s.code === code);
  const reports = data?.reports || [];
  const catalogMap = useMemo(
    () => new Map(catalog.map((s) => [s.code, s])),
    [catalog],
  );
  const searchResults = useMemo(
    () =>
      catalog
        .filter(
          (s) =>
            !search ||
            s.name.toLowerCase().includes(search.toLowerCase()) ||
            s.code.startsWith(search),
        )
        .slice(0, 15),
    [catalog, search],
  );
  const filtered = useMemo(
    () =>
      reports.filter((r) => {
        const cutoff = new Date();
        cutoff.setMonth(cutoff.getMonth() - Number(period));
        return (
          (!publisher || r.publisher === publisher) &&
          (!reportType || r.report_type === reportType) &&
          (!query ||
            `${title(r)} ${r.summary?.one_line_summary || ""}`
              .toLowerCase()
              .includes(query.toLowerCase())) &&
          (!status ||
            (status === "financial"
              ? !!details(r)
              : status === "analyzed"
                ? !!r.summary
                : !r.summary)) &&
          (period === "all" ||
            (r.published_at &&
              r.published_at >= cutoff.toISOString().slice(0, 10)))
        );
      }),
    [reports, publisher, query, status, period, reportType],
  );
  const enriched = reports.filter((r) => details(r));
  const analyzed = reports.filter((r) => r.summary);
  const priced = analyzed.filter((r) => r.summary.target_price_new != null);
  const latest = priced[0];
  const current = reports.find((r) => r.id === focused);
  function pickStock(next, mode = "library") {
    setView("reports");
    setStockMode(mode);
    setCode(next);
    setSearch("");
    setSearchOpen(false);
  }
  function toggleReport(id) {
    setSelected((previous) =>
      previous.includes(id)
        ? previous.filter((x) => x !== id)
        : [...previous, id],
    );
  }
  function openComparison() {
    if (selected.length === 2) {
      setView("compare");
      setFocused(null);
      return;
    }
    if (selected.length) {
      setToast("비교할 단일종목 보고서 두 건을 선택해 주세요.");
      return;
    }
    const pool = enriched.length >= 2 ? enriched : analyzed;
    if (pool.length >= 2) {
      setSelected([
        pool[0].id,
        (
          pool.find((r, i) => i > 0 && r.publisher === pool[0].publisher) ||
          pool[1]
        ).id,
      ]);
      setView("compare");
      setFocused(null);
    } else setToast("목록에서 비교할 보고서 두 개를 선택해 주세요.");
  }
  async function toggleFavorite() {
    try {
      const x = await api(`/favorites/${code}`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !favorites.includes(code) }),
      });
      setFavorites(x.favorites);
    } catch (e) {
      setToast(e.message);
    }
  }
  async function runAnalysis(rows) {
    if (runningAnalysis.current || !rows.length) return;
    const chosen = rows.filter((r) => r.report_type === "단일종목");
    if (!chosen.length) {
      setToast("단일종목 보고서를 선택해 주세요.");
      return;
    }
    runningAnalysis.current = true;
    cancelAnalysis.current = false;
    setBusy(chosen.length === 1 ? chosen[0].id : "batch");
    const results = chosen.map((r) => ({
      id: r.id,
      title: title(r),
      status: "waiting",
    }));
    const publish = () =>
      setAnalysisProgress({
        items: results.map((x) => ({ ...x })),
        running: true,
      });
    publish();
    for (const item of results) {
      if (cancelAnalysis.current) break;
      item.status = "running";
      publish();
      try {
        const updated = await api(`/reports/${item.id}/analyze`, {
          method: "POST",
        });
        item.status = updated.analysis_reused ? "cached" : "done";
        setData((previous) =>
          previous?.reports.some((x) => x.id === updated.id)
            ? {
                ...previous,
                reports: previous.reports.map((x) =>
                  x.id === updated.id ? updated : x,
                ),
              }
            : previous,
        );
      } catch (e) {
        item.status = "error";
        item.error = e.message;
      }
      publish();
    }
    results.forEach((x) => {
      if (x.status === "waiting") x.status = "cancelled";
    });
    setAnalysisProgress({
      items: results.map((x) => ({ ...x })),
      running: false,
    });
    setBusy(null);
    runningAnalysis.current = false;
  }
  const analyze = (r) => runAnalysis([r]);
  const chosenReports = reports.filter((r) => selected.includes(r.id));
  const analyzableChosen = chosenReports.filter(
    (r) => r.report_type === "단일종목",
  );
  const pageLabel =
    view === "market"
      ? "리서치 커버리지"
      : view === "review"
        ? "리포트 검토"
        : view === "themes"
          ? "테마로 기업 찾기"
          : "기업 리서치";
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            setView("reports");
          }}
        >
          <span className="brand-icon">
            <Layers3 size={21} />
          </span>
          <span>
            Research<span className="brand-light">Desk</span>
            <small>YOUR RESEARCH WORKSPACE</small>
          </span>
        </a>
        <div className="search-wrap">
          <div className="stock-search">
            <Search size={16} />
            <input
              ref={searchRef}
              aria-label="기업 검색"
              placeholder="기업명 또는 종목코드"
              value={search}
              onFocus={() => setSearchOpen(true)}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && searchResults[0])
                  pickStock(searchResults[0].code);
              }}
            />
            <kbd>⌘ K</kbd>
          </div>
          {searchOpen && (
            <>
              <button
                className="search-dismiss"
                aria-label="검색 닫기"
                onClick={() => setSearchOpen(false)}
              />
              <div className="search-results">
                {searchResults.map((s) => (
                  <button key={s.code} onClick={() => pickStock(s.code)}>
                    <span>{s.name}</span>
                    <small>{s.code}</small>
                  </button>
                ))}
                {!searchResults.length && <p>검색 결과가 없습니다.</p>}
              </div>
            </>
          )}
        </div>
        {catalogError && <p className="sidebar-error">{catalogError}</p>}
        <div className="nav-section workspace-nav">
          <span className="eyebrow">WORKSPACE</span>
          <button
            className={`nav-item ${view === "market" ? "active" : ""}`}
            onClick={() => setView("market")}
          >
            <BarChart3 size={18} />
            리서치 커버리지
          </button>
          <button
            className={`nav-item ${view === "themes" ? "active" : ""}`}
            onClick={() => setView("themes")}
          >
            <Telescope size={18} />
            테마로 기업 찾기
          </button>
          <button
            className={`nav-item ${view === "reports" ? "active" : ""}`}
            onClick={() => setView("reports")}
          >
            <Building2 size={18} />
            기업 리서치
            <span className="nav-dot" />
          </button>
          <button
            className={`nav-item ${view === "compare" ? "active" : ""}`}
            onClick={openComparison}
          >
            <Columns2 size={18} />
            보고서 비교
            {selected.length > 0 && (
              <span className="nav-count">{selected.length}</span>
            )}
          </button>
          <button
            className={`nav-item ${view === "review" ? "active" : ""}`}
            onClick={() => setView("review")}
          >
            <ClipboardCheck size={18} />
            리포트 검토
          </button>
        </div>
        <div className="nav-section">
          <span className="eyebrow">
            관심 기업 <Star size={12} />
          </span>
          {favorites.length ? (
            favorites.map((c) => (
              <button
                className={`company-nav ${c === code ? "chosen" : ""}`}
                key={c}
                onClick={() => pickStock(c)}
              >
                <span className="company-dot" />
                <span>{catalogMap.get(c)?.name || c}</span>
                <small>{c}</small>
              </button>
            ))
          ) : (
            <p className="sidebar-hint">
              기업명 옆의 별을 눌러
              <br />
              관심 기업을 모아보세요.
            </p>
          )}
        </div>
        <div className="nav-section recent">
          <span className="eyebrow">최근 열어본 기업</span>
          {recent.map((c) => (
            <button
              className={`company-nav ${c === code ? "chosen" : ""}`}
              key={c}
              onClick={() => pickStock(c)}
            >
              <span>{catalogMap.get(c)?.name || c}</span>
              <small>{c}</small>
            </button>
          ))}
        </div>
        <div className="sidebar-footer">
          <div className="local-label">
            <span />
            로컬 리서치 라이브러리
          </div>
          <p>
            보고서를 연결하고,
            <br />
            변화를 읽어보세요.
          </p>
          <span className="version">RESEARCH DESK · 01</span>
        </div>
      </aside>
      <main className="main">
        <StatusLine />
        <header className="topbar">
          <div>
            워크스페이스 <ChevronRight size={13} /> {pageLabel}{" "}
            {stock && !["market", "review", "themes"].includes(view) && (
              <>
                <ChevronRight size={13} />
                <strong>{stock.name}</strong>
              </>
            )}
          </div>
          <span>
            <BookOpen size={14} /> Equity research
          </span>
        </header>
        <div className="workspace-body">
          {view === "market" ? (
            <Coverage onStock={pickStock} />
          ) : view === "review" ? (
            <ReviewQueue onChanged={() => setReload((x) => x + 1)} />
          ) : view === "themes" ? (
            <ThemeSearch onStock={(c) => pickStock(c, "peers")} />
          ) : (
            <>
              <div className="page-heading">
                <div className="company-heading">
                  <div className="company-avatar">
                    {stock?.name?.slice(0, 1) || "R"}
                  </div>
                  <div>
                    <div className="title-line">
                      <h1>{stock?.name || code}</h1>
                      <button
                        className={`icon-button favorite ${favorites.includes(code) ? "starred" : ""}`}
                        aria-label={
                          favorites.includes(code)
                            ? "관심 기업 해제"
                            : "관심 기업 추가"
                        }
                        onClick={toggleFavorite}
                      >
                        <Star
                          size={20}
                          fill={
                            favorites.includes(code) ? "currentColor" : "none"
                          }
                        />
                      </button>
                    </div>
                    <div className="stock-meta">
                      <span>{code}</span>
                      <span className="divider-dot" />
                      <span>{stock?.sector_major || "기업 리서치"}</span>
                      <Badge>{stock?.sector_minor || "KRX"}</Badge>
                    </div>
                  </div>
                </div>
                <div className="heading-actions">
                  <button
                    className="button secondary"
                    onClick={() => setReload((x) => x + 1)}
                    disabled={loading || !!busy}
                  >
                    새로고침
                  </button>
                  <button
                    className="button primary"
                    onClick={openComparison}
                    disabled={loading || !reports.length}
                  >
                    <Columns2 size={16} />
                    보고서 비교 <ArrowUpRight size={15} />
                  </button>
                </div>
              </div>
              {loading ? (
                <div className="loading-state" role="status">
                  <LoaderCircle className="spin" />
                  <h3>기업의 리서치를 모으고 있습니다</h3>
                  <p>보고서와 저장된 금융 정보를 불러오는 중입니다.</p>
                </div>
              ) : loadError ? (
                <div role="alert" className="error-panel">
                  <h3>리포트를 불러오지 못했습니다</h3>
                  <p>{loadError}</p>
                  <button
                    className="button primary"
                    onClick={() => setReload((x) => x + 1)}
                  >
                    다시 시도
                  </button>
                </div>
              ) : view === "compare" && selected.length === 2 ? (
                <Comparison
                  key={`${code}-${selected.join("-")}`}
                  ids={selected}
                  reports={reports}
                  onBack={() => setView("reports")}
                  onChange={setSelected}
                  onAnalyze={analyze}
                  busy={busy}
                />
              ) : (
                <>
                  <div className="stats-grid">
                    <Stat
                      label="수집된 기업 리포트"
                      value={number(reports.length)}
                      unit="건"
                      note={
                        reports.length
                          ? `${date(reports.at(-1).published_at)} — ${date(reports[0].published_at)}`
                          : "현재 저장된 보고서가 없습니다"
                      }
                    />
                    <Stat
                      label="리서치 발행처"
                      value={
                        new Set(reports.map((r) => r.publisher).filter(Boolean))
                          .size
                      }
                      unit="곳"
                      note="이 기업을 다룬 발행처"
                    />
                    <Stat
                      label="금융 정보 분석"
                      value={enriched.length}
                      unit={`/ ${reports.length}건`}
                      note={`${analyzed.length}건 요약 · ${enriched.length}건 상세 분석`}
                      accent
                    />
                    <Stat
                      label="최근 분석 목표주가"
                      value={
                        latest ? number(latest.summary.target_price_new) : "—"
                      }
                      unit={latest ? "원" : ""}
                      note={
                        latest
                          ? `${latest.publisher} · ${date(latest.published_at)}`
                          : "분석된 목표주가가 없습니다"
                      }
                    />
                  </div>
                  <div className="company-subnav">
                    <button
                      className={stockMode === "library" ? "active" : ""}
                      onClick={() => setStockMode("library")}
                    >
                      리포트 라이브러리
                    </button>
                    <button
                      className={stockMode === "activity" ? "active" : ""}
                      onClick={() => setStockMode("activity")}
                    >
                      발행 추이 · 발행처
                    </button>
                    <button
                      className={stockMode === "peers" ? "active" : ""}
                      onClick={() => setStockMode("peers")}
                    >
                      유사 기업
                    </button>
                  </div>
                  {stockMode === "activity" && (
                    <StockActivity key={code} code={code} />
                  )}
                  {stockMode === "peers" && (
                    <Peers
                      key={code}
                      code={code}
                      onStock={(c) => pickStock(c, "peers")}
                    />
                  )}
                  <div hidden={stockMode !== "library"}>
                    <div className="section-intro">
                      <div>
                        <span className="eyebrow">COMPANY RESEARCH</span>
                        <h2>
                          리포트 라이브러리 <span>{reports.length}</span>
                        </h2>
                      </div>
                      <p>
                        보고서를 열어 핵심 정보를 읽고, 두 개를 골라 비교하세요.
                      </p>
                    </div>
                    <div
                      className={`library-layout ${current ? "with-detail" : ""}`}
                    >
                      <section className="library panel">
                        <div className="library-toolbar">
                          <div className="filter-search">
                            <Search size={16} />
                            <input
                              aria-label="리포트 검색"
                              placeholder="제목이나 핵심 내용 검색"
                              value={query}
                              onChange={(e) => setQuery(e.target.value)}
                            />
                          </div>
                          <div className="filters">
                            <select
                              aria-label="발행처 필터"
                              value={publisher}
                              onChange={(e) => setPublisher(e.target.value)}
                            >
                              <option value="">모든 발행처</option>
                              {[
                                ...new Set(
                                  reports
                                    .map((r) => r.publisher)
                                    .filter(Boolean),
                                ),
                              ]
                                .sort()
                                .map((p) => (
                                  <option key={p}>{p}</option>
                                ))}
                            </select>
                            <select
                              aria-label="보고서 유형 필터"
                              value={reportType}
                              onChange={(e) => setReportType(e.target.value)}
                            >
                              <option value="">모든 유형</option>
                              {[...new Set(reports.map((r) => r.report_type))]
                                .filter(Boolean)
                                .map((t) => (
                                  <option key={t}>{t}</option>
                                ))}
                            </select>
                            <select
                              aria-label="기간 필터"
                              value={period}
                              onChange={(e) => setPeriod(e.target.value)}
                            >
                              <option value="all">전체 기간</option>
                              <option value="3">최근 3개월</option>
                              <option value="6">최근 6개월</option>
                              <option value="12">최근 1년</option>
                            </select>
                            <SlidersHorizontal size={15} />
                          </div>
                        </div>
                        <div className="list-tabs">
                          <div>
                            {[
                              ["", "전체"],
                              ["financial", "금융 정보"],
                              ["analyzed", "요약 완료"],
                              ["pending", "미분석"],
                            ].map(([value, label]) => (
                              <button
                                className={status === value ? "active" : ""}
                                onClick={() => setStatus(value)}
                                key={value}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                          <span>
                            {filtered.length}건 · 최신순 <ArrowDown size={12} />
                          </span>
                        </div>
                        {filtered.length ? (
                          <div className="report-table-wrap">
                            <table className="report-table">
                              <thead>
                                <tr>
                                  <th className="check-col">
                                    <input
                                      type="checkbox"
                                      aria-label="현재 페이지 단일종목 전체 선택"
                                      checked={
                                        filtered
                                          .slice(0, pageSize)
                                          .filter(
                                            (r) => r.report_type === "단일종목",
                                          ).length > 0 &&
                                        filtered
                                          .slice(0, pageSize)
                                          .filter(
                                            (r) => r.report_type === "단일종목",
                                          )
                                          .every((r) => selected.includes(r.id))
                                      }
                                      onChange={(e) => {
                                        const ids = filtered
                                          .slice(0, pageSize)
                                          .filter(
                                            (r) => r.report_type === "단일종목",
                                          )
                                          .map((r) => r.id);
                                        setSelected((s) =>
                                          e.target.checked
                                            ? [...new Set([...s, ...ids])]
                                            : s.filter(
                                                (id) => !ids.includes(id),
                                              ),
                                        );
                                      }}
                                    />
                                  </th>
                                  <th>발행일 / 발행처</th>
                                  <th>리포트 & 핵심 내용</th>
                                  <th className="target-col">목표주가</th>
                                  <th className="status-col">분석 상태</th>
                                  <th />
                                </tr>
                              </thead>
                              <tbody>
                                {filtered.slice(0, pageSize).map((r) => (
                                  <tr
                                    key={r.id}
                                    className={`${focused === r.id ? "focused" : ""} ${selected.includes(r.id) ? "checked" : ""}`}
                                  >
                                    <td>
                                      <input
                                        type="checkbox"
                                        aria-label={`${date(r.published_at)} ${r.publisher} 보고서 선택`}
                                        disabled={r.report_type !== "단일종목"}
                                        checked={selected.includes(r.id)}
                                        onChange={() => toggleReport(r.id)}
                                      />
                                    </td>
                                    <td className="report-date">
                                      <strong>{date(r.published_at)}</strong>
                                      <span>
                                        <span className="publisher-mark">
                                          {r.publisher?.slice(0, 1) || "R"}
                                        </span>
                                        {r.publisher || "발행처 미상"}
                                      </span>
                                    </td>
                                    <td className="report-title">
                                      {r.report_type !== "단일종목" && (
                                        <Badge>{r.report_type}</Badge>
                                      )}
                                      <button onClick={() => setFocused(r.id)}>
                                        {title(r)}
                                      </button>
                                      <p>
                                        {r.summary?.one_line_summary ||
                                          "아직 요약되지 않은 보고서입니다. 원문을 열거나 분석해 보세요."}
                                      </p>
                                    </td>
                                    <td className="target-col">
                                      <strong>
                                        {r.summary?.target_price_new != null
                                          ? number(r.summary.target_price_new)
                                          : "—"}
                                      </strong>
                                      {r.summary?.target_price_dir &&
                                        r.summary.target_price_dir !==
                                          "N/A" && (
                                          <span
                                            className={`direction ${r.summary.target_price_dir === "상향" ? "up" : r.summary.target_price_dir === "하향" ? "down" : ""}`}
                                          >
                                            {r.summary.target_price_dir ===
                                            "상향"
                                              ? "↗ "
                                              : r.summary.target_price_dir ===
                                                  "하향"
                                                ? "↘ "
                                                : ""}
                                            {r.summary.target_price_dir}
                                          </span>
                                        )}
                                    </td>
                                    <td className="status-col">
                                      <Badge
                                        tone={
                                          details(r)
                                            ? "green"
                                            : r.summary
                                              ? "blue"
                                              : ""
                                        }
                                      >
                                        {details(r)
                                          ? "금융 정보"
                                          : r.summary
                                            ? "요약 완료"
                                            : "미분석"}
                                      </Badge>
                                    </td>
                                    <td>
                                      <button
                                        className="icon-button"
                                        aria-label={`${title(r)} 상세 열기`}
                                        onClick={() => setFocused(r.id)}
                                      >
                                        <ChevronRight size={16} />
                                      </button>
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        ) : (
                          <Empty
                            title={
                              reports.length
                                ? "조건에 맞는 보고서가 없습니다"
                                : "아직 수집된 기업 리포트가 없습니다"
                            }
                          >
                            {reports.length
                              ? "검색어나 필터를 바꿔보세요."
                              : "다른 기업을 검색하거나 수집 후 새로고침해 주세요."}
                          </Empty>
                        )}
                        {filtered.length > pageSize && (
                          <button
                            className="load-more"
                            onClick={() => setPageSize((n) => n + 20)}
                          >
                            리포트 더 보기 ·{" "}
                            {Math.min(pageSize, filtered.length)} /{" "}
                            {filtered.length}건
                          </button>
                        )}
                        <div className="table-footer">
                          <span>
                            <Check size={13} />
                            저장된 원문 및 분석 기준
                          </span>
                          <span>선택한 리포트만 분석 · 두 건 선택 시 비교</span>
                        </div>
                      </section>
                      {current && (
                        <ReportDetail
                          key={current.id}
                          report={current}
                          onClose={() => setFocused(null)}
                          onAnalyze={analyze}
                          busy={busy}
                          onToggle={() => toggleReport(current.id)}
                          selected={selected.includes(current.id)}
                        />
                      )}
                    </div>
                    {!current && analyzed.length > 0 && (
                      <div className="research-bottom">
                        <section className="insight-panel">
                          <div className="insight-icon">
                            <Sparkles size={20} />
                          </div>
                          <div>
                            <span className="eyebrow">최근 분석의 핵심</span>
                            <h3>{analyzed[0].summary.one_line_summary}</h3>
                            <p>
                              {analyzed[0].publisher} ·{" "}
                              {date(analyzed[0].published_at)}
                              <button
                                onClick={() => setFocused(analyzed[0].id)}
                              >
                                리포트 살펴보기 <ArrowRight size={14} />
                              </button>
                            </p>
                          </div>
                        </section>
                        <section className="workflow-note">
                          <Columns2 size={23} />
                          <div>
                            <h4>무엇이 달라졌을까요?</h4>
                            <p>
                              목표주가, 실적 추정, 투자 논리를
                              <br />
                              같은 화면에서 나란히 확인하세요.
                            </p>
                          </div>
                        </section>
                      </div>
                    )}
                  </div>
                </>
              )}
            </>
          )}
          <footer className="page-footer">
            <span>Research Desk</span>
            <span>수집된 보고서 기준 · 목표주가는 실시간 주가와 다릅니다</span>
          </footer>
        </div>
      </main>
      {selected.length > 0 && view === "reports" && (
        <div className="compare-dock">
          <div className="dock-icon">
            <Columns2 size={18} />
          </div>
          <span>
            <strong>{selected.length}건</strong> 선택
          </span>
          <div className="dock-reports">
            {selected.slice(0, 2).map((id) => {
              const r = reports.find((x) => x.id === id);
              return (
                r && (
                  <button key={id} onClick={() => toggleReport(id)}>
                    {r.publisher} · {date(r.published_at)}
                    <X size={13} />
                  </button>
                )
              );
            })}
            {selected.length > 2 && (
              <span className="dock-placeholder">
                외 {selected.length - 2}건
              </span>
            )}
            {selected.length === 1 && (
              <span className="dock-placeholder">
                두 건을 선택하면 비교할 수 있습니다
              </span>
            )}
          </div>
          <button
            className="button primary"
            disabled={!!busy || !analyzableChosen.length}
            onClick={() => runAnalysis(analyzableChosen)}
          >
            <Sparkles size={14} />
            선택한 {analyzableChosen.length}건 분석
          </button>
          <button
            className="button secondary"
            disabled={selected.length !== 2 || analyzableChosen.length !== 2}
            onClick={openComparison}
          >
            비교 열기 <ArrowRight size={15} />
          </button>
          <button
            className="icon-button"
            aria-label="리포트 선택 초기화"
            onClick={() => setSelected([])}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {analysisProgress && (
        <section
          className="analysis-progress"
          aria-label="선택 리포트 분석 진행"
        >
          <header>
            <div>
              <Sparkles size={15} />
              <strong>선택한 {analysisProgress.items.length}건만 분석</strong>
            </div>
            {analysisProgress.running ? (
              <button
                onClick={() => {
                  cancelAnalysis.current = true;
                  setToast("진행 중인 보고서를 마친 뒤 멈춥니다.");
                }}
              >
                남은 분석 중지
              </button>
            ) : (
              <button
                aria-label="분석 진행 닫기"
                onClick={() => setAnalysisProgress(null)}
              >
                <X size={15} />
              </button>
            )}
          </header>
          <p>
            완료{" "}
            {
              analysisProgress.items.filter((x) =>
                ["done", "cached"].includes(x.status),
              ).length
            }{" "}
            · 실패{" "}
            {analysisProgress.items.filter((x) => x.status === "error").length}{" "}
            · 기존 상세 분석은 재사용합니다.
          </p>
          <progress
            value={
              analysisProgress.items.filter(
                (x) => !["waiting", "running"].includes(x.status),
              ).length
            }
            max={analysisProgress.items.length}
          />
          <details>
            <summary>선택한 보고서별 진행 결과</summary>
            {analysisProgress.items.map((x) => (
              <div className="analysis-result" key={x.id}>
                <span>{x.title}</span>
                <strong>
                  {
                    {
                      waiting: "대기",
                      running: "분석 중",
                      done: "완료",
                      cached: "기존 분석 재사용",
                      error: "실패",
                      cancelled: "중지",
                    }[x.status]
                  }
                </strong>
                {x.error && <small>{x.error}</small>}
              </div>
            ))}
          </details>
          {!analysisProgress.running &&
            analysisProgress.items.some((x) => x.status === "error") && (
              <button
                className="button secondary"
                onClick={() =>
                  runAnalysis(
                    analysisProgress.items
                      .filter((x) => x.status === "error")
                      .map((x) => ({
                        id: x.id,
                        title: x.title,
                        report_type: "단일종목",
                      })),
                  )
                }
              >
                실패한 선택 리포트만 재시도
              </button>
            )}
        </section>
      )}
      {toast && (
        <div className="toast" role="status">
          {toast}
          <button
            className="icon-button"
            aria-label="알림 닫기"
            onClick={() => setToast("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
function Stat({ label, value, unit, note, accent }) {
  return (
    <div className={`stat ${accent ? "accent" : ""}`}>
      <span>{label}</span>
      <div>
        <strong>{value}</strong>
        <small>{unit}</small>
      </div>
      <p>{note}</p>
    </div>
  );
}
function ReportDetail({
  report: r,
  onClose,
  onAnalyze,
  busy,
  onToggle,
  selected,
}) {
  const [tab, setTab] = useState("summary");
  const s = r.summary,
    f = details(r);
  return (
    <aside className="detail-panel panel" key={r.id}>
      <div className="detail-top">
        <span>
          <FileText size={15} />
          리포트 상세
        </span>
        <button
          className="icon-button"
          aria-label="리포트 상세 닫기"
          onClick={onClose}
        >
          <PanelRightClose size={18} />
        </button>
      </div>
      <div className="detail-content">
        <div className="detail-meta">
          <Badge>{r.publisher}</Badge>
          <span>{date(r.published_at)}</span>
        </div>
        <h2>{title(r)}</h2>
        <div className="metadata-facts">
          <Badge>{r.report_type}</Badge>
          {[...(r.sectors_major || []), ...(r.sectors_minor || [])].map(
            (x, i) => (
              <Badge key={i}>{x}</Badge>
            ),
          )}
          {r.company_names?.length > 0 && (
            <p>관련 기업: {r.company_names.join(", ")}</p>
          )}
          {r.products?.length > 0 && <p>제품: {r.products.join(", ")}</p>}
        </div>
        <div className="detail-actions">
          <a
            className="button secondary"
            href={r.pdf_url}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} />
            원문 PDF
          </a>
          <button
            className={`button ${selected ? "selected-button" : "secondary"}`}
            onClick={onToggle}
            disabled={r.report_type !== "단일종목"}
          >
            {selected ? <Check size={14} /> : <Plus size={14} />}
            {selected ? "선택됨" : "선택 담기"}
          </button>
        </div>
        {s && (
          <>
            <div className="detail-price">
              <div>
                <span>목표주가</span>
                <strong>{price(s.target_price_new)}</strong>
                {s.target_price_old != null && (
                  <small>이전 {price(s.target_price_old)}</small>
                )}
              </div>
              <div>
                <span>투자의견</span>
                <strong className="rating-label">{rating(r)}</strong>
                <small>
                  {s.recommendation_dir !== "N/A" ? s.recommendation_dir : ""}
                </small>
              </div>
            </div>
            <p className="summary-callout">{s.one_line_summary}</p>
          </>
        )}
        {!f && r.report_type === "단일종목" && (
          <div className="analyze-callout">
            <Sparkles size={18} />
            <div>
              <h4>
                {s ? "금융 정보를 더 자세히" : "이 리포트의 핵심을 읽어보세요"}
              </h4>
              <p>실적 전망, 밸류에이션, 투자 논리를 추출합니다.</p>
              <button
                className="button primary"
                disabled={!!busy}
                onClick={() => onAnalyze(r)}
              >
                {busy === r.id ? (
                  <>
                    <LoaderCircle size={14} className="spin" />
                    분석 중…
                  </>
                ) : (
                  "이 보고서 분석"
                )}
              </button>
            </div>
          </div>
        )}
        {s && (
          <>
            <div
              className="detail-tabs"
              role="tablist"
              aria-label="리포트 정보"
            >
              {[
                ["summary", "핵심 요약"],
                ["financial", "실적 전망"],
                ["thesis", "투자 논리"],
                ["valuation", "밸류에이션"],
              ].map(([value, label]) => (
                <button
                  role="tab"
                  aria-selected={tab === value}
                  className={tab === value ? "active" : ""}
                  key={value}
                  onClick={() => setTab(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            {tab === "summary" && (
              <>
                <Points heading="투자 포인트" items={s.positive_points} />
                <Points heading="확인할 리스크" items={s.risk_points} risk />
                {f?.catalysts?.length > 0 && (
                  <section className="catalysts">
                    <h4>앞으로의 촉매</h4>
                    {f.catalysts.map((c, i) => (
                      <div key={i}>
                        <span>{c.expected_timing || "시점 미기재"}</span>
                        <strong>{c.event}</strong>
                        {c.condition && <p>{c.condition}</p>}
                        <Source evidence={c.evidence} report={r} />
                      </div>
                    ))}
                  </section>
                )}
              </>
            )}
            {tab === "financial" && <MetricList report={r} />}
            {tab === "thesis" && <SingleTheses report={r} />}
            {tab === "valuation" && <ValuationView report={r} />}
            <details className="source-meta">
              <summary>추출 근거와 분석 범위</summary>
              <p>목표주가 원문: {s.target_price_raw || "미기재"}</p>
              <p>투자의견 원문: {s.recommendation_raw || "미기재"}</p>
              <p>추출 신뢰도: {s.extraction_confidence || "미기재"}</p>
              <p>
                참고 페이지:{" "}
                {(s.source_pages || []).map((n) => (
                  <a
                    key={n}
                    href={`${r.pdf_url}#page=${n}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    p.{n}
                  </a>
                ))}
              </p>
              {s.input_truncated && (
                <p className="truncated">
                  일부 페이지만 분석됨 · {s.input_pages_used}/
                  {s.input_total_pages}페이지
                </p>
              )}
              {f?.unsupported_numeric_values > 0 && (
                <p>
                  원문 근거와 일치하지 않은 수치 {f.unsupported_numeric_values}
                  개 제외
                </p>
              )}
            </details>
            <p className="model-note">
              {s.llm_model || "AI"} 분석 · 근거와 원문을 함께 확인하세요.
            </p>
          </>
        )}
      </div>
    </aside>
  );
}
function SingleTheses({ report }) {
  const theses = details(report)?.theses || [];
  if (!theses.length)
    return (
      <Empty title="추출된 투자 논리가 없습니다">
        금융 정보 분석 후 확인할 수 있습니다.
      </Empty>
    );
  return (
    <div>
      {theses.map((t, i) => (
        <section className="single-thesis" key={i}>
          <Badge>{t.support_type}</Badge>
          <h4>{t.claim}</h4>
          <p>{t.mechanism}</p>
          {t.monitoring_metric && (
            <div className="monitor">
              <strong>관찰할 지표</strong>
              {t.monitoring_metric}
            </div>
          )}
          {t.invalidation_condition && (
            <div className="invalidation">
              <strong>
                논리를 재검토할 조건 <small>{t.invalidation_basis}</small>
              </strong>
              {t.invalidation_condition}
            </div>
          )}
          <Source evidence={t.evidence} report={report} />
        </section>
      ))}
    </div>
  );
}
function MetricList({ report }) {
  const metrics = details(report)?.metrics || [];
  if (!metrics.length)
    return (
      <Empty title="추출된 실적 정보가 없습니다">
        금융 정보 분석 후 확인할 수 있습니다.
      </Empty>
    );
  return (
    <div className="metric-list">
      {metrics.map((m, i) => (
        <div className="metric-item" key={i}>
          <div>
            <strong>{m.metric}</strong>
            <span>
              {m.fiscal_period || "기간 미기재"} · {m.value_type} ·{" "}
              {m.accounting_basis} · {m.scenario}
            </span>
          </div>
          <div className="metric-number">
            {number(m.value)}{" "}
            <small>
              {m.unit}
              {m.currency && ` · ${m.currency}`}
            </small>
          </div>
          {m.previous_value != null && (
            <div className="metric-previous">
              문서 내 이전 추정{" "}
              <strong>
                {number(m.previous_value)} {m.unit}
              </strong>{" "}
              → {number(m.value)} {m.unit}
              <Source evidence={m.previous_evidence} report={report} />
            </div>
          )}
          <Source evidence={m.evidence} report={report} />
        </div>
      ))}
    </div>
  );
}
function ValuationView({ report }) {
  const v = details(report)?.valuation;
  if (!v)
    return (
      <Empty title="추출된 밸류에이션이 없습니다">
        금융 정보 분석 후 확인할 수 있습니다.
      </Empty>
    );
  return (
    <div className="valuation">
      <div className="valuation-method">
        <span>평가 방법</span>
        <strong>{v.method}</strong>
        <small>{v.target_horizon || "평가 시점 미기재"}</small>
      </div>
      {v.explanation && <p>{v.explanation}</p>}
      {details(report)?.rating && (
        <section className="source-meta">
          <strong>투자의견 원문</strong>
          <p>
            {details(report).rating.previous_label || "이전 미기재"} →{" "}
            {details(report).rating.current_label || "미기재"}
          </p>
          <p>{details(report).rating.definition}</p>
          <p>{details(report).rating.horizon}</p>
          <Source evidence={details(report).rating.evidence} report={report} />
        </section>
      )}
      <h4>주요 가정</h4>
      {v.assumptions?.map((a, i) => (
        <div className="assumption" key={i}>
          <strong>{a.name}</strong>
          <div>
            {a.previous && (
              <>
                <del>{a.previous}</del>
                <ArrowRight size={12} />
              </>
            )}
            {a.current}
          </div>
          {a.fiscal_period && <small>{a.fiscal_period}</small>}
          <Source evidence={a.evidence} report={report} />
        </div>
      ))}
      {v.change_drivers?.length > 0 && <h4>목표주가 변화의 근거</h4>}
      {v.change_drivers?.map((d, i) => (
        <div className="assumption" key={i}>
          <Badge>{d.category}</Badge>
          <p>{d.explanation}</p>
          <Source evidence={d.evidence} report={report} />
        </div>
      ))}
    </div>
  );
}
function Comparison({ ids, reports, onBack, onChange, onAnalyze, busy }) {
  const pairKey = `${ids[0]}:${ids[1]}`;
  const currentPair = useRef(pairKey);
  currentPair.current = pairKey;
  const [pairBusy, setPairBusy] = useState(false),
    [pairError, setPairError] = useState("");
  async function analyzePair() {
    const requestedPair = pairKey;
    setPairBusy(true);
    setPairError("");
    try {
      const analyzedPair = await api("/compare/analyze", {
        method: "POST",
        body: JSON.stringify({ left: ids[0], right: ids[1] }),
      });
      if (currentPair.current === requestedPair) setResult(analyzedPair);
    } catch (e) {
      if (currentPair.current === requestedPair) setPairError(e.message);
    } finally {
      setPairBusy(false);
    }
  }
  const [result, setResult] = useState(null),
    [error, setError] = useState(""),
    [tab, setTab] = useState("changes");
  const reportA = reports.find((r) => r.id === ids[0]),
    reportB = reports.find((r) => r.id === ids[1]);
  useEffect(() => {
    const controller = new AbortController();
    setResult(null);
    setError("");
    setPairError("");
    api(`/compare?left=${ids[0]}&right=${ids[1]}`, {
      signal: controller.signal,
    })
      .then(setResult)
      .catch((e) => {
        if (e.name !== "AbortError") setError(e.message);
      });
    return () => controller.abort();
  }, [ids[0], ids[1], reportA?.summary, reportB?.summary]);
  const ordered = [reportA, reportB].filter(Boolean).sort(chronological);
  const left = result?.left || ordered[0],
    right = result?.right || ordered[1];
  if (!left || !right)
    return (
      <Empty title="선택된 보고서를 찾을 수 없습니다">
        목록에서 다시 선택해 주세요.
      </Empty>
    );
  const same = result
    ? result.same_publisher
    : !!left.publisher && left.publisher === right.publisher;
  return (
    <section className="comparison">
      <div className="comparison-heading">
        <button className="text-button" onClick={onBack}>
          <ArrowLeft size={15} />
          리포트 목록
        </button>
        <Badge tone={same ? "green" : "blue"}>
          {same ? "동일 발행처 · 전망 변화" : "발행처 간 · 시각 비교"}
        </Badge>
      </div>
      <div className="compare-title">
        <div>
          <span className="eyebrow">READ THE CHANGE</span>
          <h2>두 보고서, 한눈에.</h2>
          <p>
            {same
              ? "같은 발행처가 기업을 바라보는 관점이 어떻게 달라졌는지 확인하세요."
              : "발행처별 관점의 차이입니다. 동일 애널리스트의 전망 수정이나 시장 컨센서스를 뜻하지 않습니다."}
          </p>
        </div>
        <Columns2 size={34} />
      </div>
      <div className="compare-report-heads">
        {[left, right].map((r, i) => (
          <div className={`compare-report-head ${i ? "newer" : ""}`} key={r.id}>
            <div className="compare-label">
              <span>{i ? "B · 비교 보고서" : "A · 기준 보고서"}</span>
              <a href={r.pdf_url} target="_blank" rel="noreferrer">
                원문 PDF <ArrowUpRight size={13} />
              </a>
            </div>
            <label className="sr-only" htmlFor={`report-selector-${i}`}>
              {i ? "비교 B 보고서" : "비교 A 보고서"}
            </label>
            <select
              id={`report-selector-${i}`}
              value={r.id}
              onChange={(e) =>
                onChange(
                  i
                    ? [left.id, Number(e.target.value)]
                    : [Number(e.target.value), right.id],
                )
              }
            >
              {reports
                .filter((r) => r.report_type === "단일종목")
                .map((option) => (
                  <option
                    disabled={option.id === (i ? left.id : right.id)}
                    key={option.id}
                    value={option.id}
                  >
                    {date(option.published_at)} · {option.publisher} ·{" "}
                    {title(option)}
                  </option>
                ))}
            </select>
            <h3>{title(r)}</h3>
            <p>
              {r.summary?.one_line_summary ||
                "아직 분석되지 않은 보고서입니다."}
            </p>
            <div className="compare-values">
              <div>
                <span>목표주가</span>
                <strong>{price(r.summary?.target_price_new)}</strong>
              </div>
              <div>
                <span>투자의견</span>
                <strong className="rating-label">{rating(r)}</strong>
              </div>
            </div>
            {!details(r) && r.report_type === "단일종목" && (
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => onAnalyze(r)}
              >
                {busy === r.id ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Sparkles size={14} />
                )}{" "}
                {busy === r.id
                  ? "금융 정보 분석 중…"
                  : "이 보고서 금융 정보 분석"}
              </button>
            )}
          </div>
        ))}
      </div>
      {result && (
        <div className="change-strip">
          {result.target_price_change && (
            <div>
              <span>
                목표주가 차이 <small>A → B</small>
              </span>
              <strong
                className={
                  result.target_price_change.delta > 0
                    ? "up"
                    : result.target_price_change.delta < 0
                      ? "down"
                      : ""
                }
              >
                {result.target_price_change.change_label}
              </strong>
              <small>{number(result.target_price_change.delta)}원</small>
            </div>
          )}
          {result.metrics
            .filter((m) => ["EPS", "ROE"].includes(m.metric))
            .slice(0, 2)
            .map((m, i) => (
              <div key={i}>
                <span>
                  {m.metric}{" "}
                  <small>
                    {m.fiscal_period} · {m.accounting_basis}
                  </small>
                </span>
                <strong
                  className={m.delta > 0 ? "up" : m.delta < 0 ? "down" : ""}
                >
                  {m.change_label}
                </strong>
                <small>
                  {number(m.previous)} → {number(m.current)} {m.unit}
                </small>
              </div>
            ))}
        </div>
      )}
      <div
        className="compare-tabs"
        role="tablist"
        aria-label="보고서 비교 항목"
      >
        {[
          ["changes", "핵심 차이"],
          ["thesis", "투자 논리"],
          ["valuation", "밸류에이션"],
          ["pdf", "원문 나란히"],
        ].map(([value, label]) => (
          <button
            key={value}
            role="tab"
            aria-selected={tab === value}
            className={tab === value ? "active" : ""}
            onClick={() => setTab(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {result && !result.narrative && (
        <div className="pair-analysis-action">
          <button
            className="button secondary"
            disabled={pairBusy || !!busy || !left.summary || !right.summary}
            onClick={analyzePair}
          >
            {pairBusy ? (
              <LoaderCircle className="spin" size={14} />
            ) : (
              <Sparkles size={14} />
            )}
            선택한 두 보고서 비교 해석
          </button>
          <p>저장된 두 보고서의 분석만 사용합니다.</p>
          {pairError && <p role="alert">{pairError}</p>}
        </div>
      )}
      {error ? (
        <div className="error-panel" role="alert">
          {error}
        </div>
      ) : !result ? (
        <div className="comparison-loading" role="status">
          <LoaderCircle className="spin" size={18} />두 보고서의 금융 정보를
          맞추고 있습니다.
        </div>
      ) : tab === "changes" ? (
        <>
          {result.narrative && (
            <div className="comparison-narrative">
              <Sparkles size={20} />
              <div>
                <h4>변화를 읽는 핵심</h4>
                <p>{result.narrative}</p>
              </div>
            </div>
          )}
          <section className="panel comparison-metrics">
            <div className="panel-heading">
              <div>
                <h3>실적 추정 비교</h3>
                <p>
                  기간 · 단위 · 회계 기준 · 시나리오가 일치하는 추정치만
                  비교합니다.
                </p>
              </div>
              <Badge>{result.metrics.length}개 지표</Badge>
            </div>
            {result.metrics.length > 0 ? (
              <div className="report-table-wrap">
                <table className="comparison-table">
                  <thead>
                    <tr>
                      <th>지표 / 기준</th>
                      <th>A · {date(left.published_at)}</th>
                      <th>B · {date(right.published_at)}</th>
                      <th>{same ? "변화" : "차이 (B − A)"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.metrics.map((m, i) => (
                      <tr key={i}>
                        <td>
                          <strong>{m.metric}</strong>
                          <small>
                            {m.fiscal_period} · {m.accounting_basis} ·{" "}
                            {m.scenario} · {m.currency || ""} {m.unit}
                          </small>
                        </td>
                        <td>
                          <strong>{number(m.previous)}</strong>
                          <Source
                            evidence={m.previous_evidence}
                            report={left}
                          />
                        </td>
                        <td className="newer-cell">
                          <strong>{number(m.current)}</strong>
                          <Source
                            evidence={m.current_evidence}
                            report={right}
                          />
                        </td>
                        <td>
                          <span
                            className={`change-value ${m.delta > 0 ? "up" : m.delta < 0 ? "down" : ""}`}
                          >
                            {m.change_label}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="같은 기준으로 비교할 실적 추정치가 없습니다">
                두 보고서의 분석 여부와 추정 기간을 확인해 주세요. 서로 다른
                기간이나 단위의 수치는 합치지 않습니다.
              </Empty>
            )}
          </section>
          <div className="side-by-side">
            {[left, right].map((r, i) => (
              <section className="panel comparison-points" key={r.id}>
                <div className="column-label">
                  {i ? "B" : "A"} · {r.publisher} · {date(r.published_at)}
                </div>
                <Points
                  heading="투자 포인트"
                  items={r.summary?.positive_points}
                />
                <Points
                  heading="확인할 리스크"
                  items={r.summary?.risk_points}
                  risk
                />
              </section>
            ))}
          </div>
        </>
      ) : tab === "thesis" ? (
        <div className="side-by-side">
          {[left, right].map((r, i) => (
            <section className="panel thesis-panel" key={r.id}>
              <div className="column-label">
                {i ? "B" : "A"} · {r.publisher} · {date(r.published_at)}
              </div>
              {details(r)?.theses?.length ? (
                details(r).theses.map((t, j) => (
                  <article className="thesis" key={j}>
                    <Badge>{t.support_type}</Badge>
                    <h3>{t.claim}</h3>
                    <p>{t.mechanism}</p>
                    {t.monitoring_metric && (
                      <div className="monitor">
                        <strong>관찰할 지표</strong>
                        {t.monitoring_metric}
                      </div>
                    )}
                    {t.invalidation_condition && (
                      <div className="invalidation">
                        <strong>
                          논리를 재검토할 조건{" "}
                          <small>{t.invalidation_basis}</small>
                        </strong>
                        {t.invalidation_condition}
                      </div>
                    )}
                    <Source evidence={t.evidence} report={r} />
                  </article>
                ))
              ) : (
                <Empty title="투자 논리가 아직 추출되지 않았습니다">
                  보고서 금융 정보를 분석해 주세요.
                </Empty>
              )}
              {details(r)?.catalysts?.length > 0 && (
                <div className="catalysts">
                  <h4>앞으로의 촉매</h4>
                  {details(r).catalysts.map((c, j) => (
                    <div key={j}>
                      <span>{c.expected_timing || "시점 미기재"}</span>
                      <strong>{c.event}</strong>
                      {c.condition && <p>{c.condition}</p>}
                      <Source evidence={c.evidence} report={r} />
                    </div>
                  ))}
                </div>
              )}
            </section>
          ))}
        </div>
      ) : tab === "valuation" ? (
        <div className="side-by-side">
          {[left, right].map((r, i) => (
            <section className="panel valuation-panel" key={r.id}>
              <div className="column-label">
                {i ? "B" : "A"} · {r.publisher} · {date(r.published_at)}
              </div>
              <ValuationView report={r} />
            </section>
          ))}
        </div>
      ) : (
        <div className="side-by-side pdf-columns">
          {[left, right].map((r, i) => (
            <section key={r.id} className="panel">
              <div className="column-label">
                {i ? "B" : "A"} · {r.publisher} · {date(r.published_at)}
                <a href={r.pdf_url} target="_blank" rel="noreferrer">
                  새 탭 <ExternalLink size={12} />
                </a>
              </div>
              <iframe
                title={`${i ? "B" : "A"} 보고서 PDF`}
                src={`${r.pdf_url}#view=FitH`}
              />
            </section>
          ))}
        </div>
      )}
    </section>
  );
}
createRoot(document.getElementById("root")).render(<App />);
