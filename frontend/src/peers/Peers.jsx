import React, { useEffect, useMemo, useState } from "react";
import { LoaderCircle, SearchX } from "lucide-react";
import { api } from "../api";
import PeerTable from "./PeerTable";
import {
  DEFAULT_WINDOW,
  WINDOWS,
  basisText,
  count,
  dotDate,
  marks,
  pct,
  pp,
  share,
  tone,
  windowLabel,
} from "./labels";
import "./peers.css";

// Fixed answers of GET /api/stocks/{code}/peers that get their own state on this screen.
const NO_PEER_DATA = "이 종목은 유사 기업 자료가 없습니다.";
const NO_SEGMENT = "그 사업부문이 없습니다.";
const NOT_JUDGED =
  "시드가 시장보다 10%p 이상 오르지 않아 반응을 판정하지 않습니다";
const NO_SEED_PRICE = "기준 회사의 주가 자료가 없어 반응을 판정하지 않습니다";

// Row filters, all off at first (spec §12.5): [key, label, a row kept when on].
const FILTERS = [
  ["candidate", "후보만", (p) => p.candidate === true],
  ["noReports", "리포트 없음만", (p) => p.coverage?.label === "none"],
  [
    "lagging",
    "덜 오름만",
    (p) => p.reaction === "none" || p.reaction === "partial",
  ],
  ["otherIndustry", "같은 산업분류 숨기기", (p) => p.same_industry !== true],
];

// The company page's 유사 기업 tab: the seed, its segment chips, the window, the filters and the
// peers list of GET /api/stocks/{code}/peers.
export default function Peers({ code, onStock }) {
  const [period, setPeriod] = useState(DEFAULT_WINDOW),
    [segment, setSegment] = useState(null),
    [filters, setFilters] = useState([]),
    [minValue, setMinValue] = useState(""),
    [data, setData] = useState(null),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    const q = new URLSearchParams({ window: period });
    if (segment != null) q.set("segment", segment);
    api(`/stocks/${encodeURIComponent(code)}/peers?${q}`, {
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
  }, [code, period, segment, retry]);
  const peers = data?.peers || [];
  const visible = useMemo(() => {
    const floor = Number(minValue) > 0 ? Number(minValue) * 1e8 : null;
    return peers.filter(
      (p) =>
        FILTERS.every(([key, , keep]) => !filters.includes(key) || keep(p)) &&
        (floor == null || (p.price?.avg_value_20d ?? -Infinity) >= floor),
    );
  }, [peers, filters, minValue]);
  function open(next) {
    onStock(next);
    window.scrollTo({ top: 0 });
  }
  const toggleFilter = (key) =>
    setFilters((f) =>
      f.includes(key) ? f.filter((x) => x !== key) : [...f, key],
    );

  if (error === NO_PEER_DATA)
    return (
      <section className="peers-section">
        <div className="panel peers-empty">
          <SearchX size={28} />
          <h3>{error}</h3>
          <p>
            신규 상장으로 사업보고서가 아직 없거나, 유사 기업 분석 대상이 아닌
            회사일 수 있습니다.
          </p>
        </div>
      </section>
    );
  if (!data)
    return (
      <section className="peers-section">
        {error ? (
          <div className="error-panel" role="alert">
            <p>{error}</p>
            <button
              className="button secondary"
              onClick={() => setRetry((x) => x + 1)}
            >
              다시 시도
            </button>
          </div>
        ) : (
          <div className="comparison-loading" role="status">
            <LoaderCircle className="spin" size={17} />
            유사 기업을 찾고 있습니다.
          </div>
        )}
      </section>
    );

  const seed = data.seed || {};
  const profile = seed.profile || {};
  const segments = profile.segments || [];
  const activeSegment = segment ?? data.segment?.no;
  const seedReturn = seed.price?.returns?.[data.window];
  return (
    <section className="peers-section">
      <div className="peers-heading">
        <div>
          <span className="eyebrow">PEER DISCOVERY</span>
          <h2>유사 기업</h2>
          <p className="peers-seed">
            <strong>{seed.name || code}</strong>
            <span>{seed.market || "시장 미상"}</span>
            {profile.niche_industry && <span>{profile.niche_industry}</span>}
            {marks(seed).map((m) => (
              <span className="badge peer-mark" key={m}>
                {m}
              </span>
            ))}
          </p>
          <p className="peers-basis">
            기준 {basisText(data.basis)} · 주가 기준일{" "}
            {data.price_as_of ? dotDate(data.price_as_of) : "자료 없음"}
          </p>
        </div>
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
      </div>
      {segments.length >= 2 && (
        <div className="peers-chips" role="group" aria-label="기준 사업부문">
          <span>기준 부문</span>
          {segments.map((s) => (
            <button
              key={s.no}
              type="button"
              className={s.no === activeSegment ? "active" : ""}
              aria-pressed={s.no === activeSegment}
              onClick={() => setSegment(s.no)}
            >
              {s.name || "이름 없는 부문"}
              <small>{share(s.revenue_share_pct)}</small>
            </button>
          ))}
        </div>
      )}
      <div className="peers-filters">
        {FILTERS.map(([key, name]) => (
          <label key={key}>
            <input
              type="checkbox"
              checked={filters.includes(key)}
              onChange={() => toggleFilter(key)}
            />
            {name}
          </label>
        ))}
        <label className="peers-min-value">
          최소 거래대금
          <input
            type="number"
            min="0"
            step="any"
            inputMode="decimal"
            placeholder="0"
            aria-label="최소 20일 평균 거래대금(억 원)"
            value={minValue}
            onChange={(e) => setMinValue(e.target.value)}
          />
          억
        </label>
        <span className="peers-count">
          {count(visible.length)} / {count(peers.length)}곳
        </span>
      </div>
      {loading ? (
        <div className="comparison-loading" role="status">
          <LoaderCircle className="spin" size={17} />
          유사 기업을 다시 불러오고 있습니다.
        </div>
      ) : error ? (
        <div className="error-panel" role="alert">
          <p>{error}</p>
          {error === NO_SEGMENT ? (
            <button
              className="button secondary"
              onClick={() => setSegment(null)}
            >
              기본 부문으로 보기
            </button>
          ) : (
            <button
              className="button secondary"
              onClick={() => setRetry((x) => x + 1)}
            >
              다시 시도
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="peers-seed-line">
            <p>
              기준 회사 {windowLabel(data.window)} 시장 대비{" "}
              <strong className={tone(data.seed_excess_pct)}>
                {data.seed_excess_pct == null
                  ? "자료 없음"
                  : pp(data.seed_excess_pct)}
              </strong>
              {seedReturn != null && <small> · 단순 {pct(seedReturn)}</small>}
            </p>
            {!data.judgeable && (
              <p className="peers-not-judged">
                {data.seed_excess_pct == null ? NO_SEED_PRICE : NOT_JUDGED}
              </p>
            )}
          </div>
          <div className="panel peers-panel">
            {visible.length ? (
              <PeerTable
                peers={visible}
                seed={seed}
                chosen={data.segment}
                period={data.window}
                onStock={open}
              />
            ) : (
              <div className="peers-empty">
                <SearchX size={26} />
                <h3>
                  {peers.length
                    ? "조건에 맞는 회사가 없습니다"
                    : "비슷한 회사를 찾지 못했습니다"}
                </h3>
                <p>
                  {peers.length
                    ? "필터를 끄거나 최소 거래대금을 낮춰 보세요."
                    : segments.length >= 2
                      ? "다른 사업부문을 기준으로 바꿔 보세요."
                      : "관련 등급 이상으로 비슷한 회사가 없습니다."}
                </p>
              </div>
            )}
          </div>
          <p className="peers-footnote">
            시드는 지금 보고 있는 기준 회사입니다. 리포트 수는 최근 1년 기준이며,
            종목은 증권사가 이 회사만 다룬 리포트, 섹터는 증권사 섹터 리포트,
            기타는 독립리서치·IR 자료 등입니다. 시장 대비 수익률은 같은 시장 종목
            수익률의 중앙값을 뺀 값입니다. 후보는 종목 리포트가 없고, 덜
            올랐고(미반응·부분반응), 거래 중이며 거래대금이 기준 이상인
            지주회사 아닌 회사입니다.
          </p>
        </>
      )}
    </section>
  );
}
