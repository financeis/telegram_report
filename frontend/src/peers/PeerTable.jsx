import React, { useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import {
  COVERAGE,
  REACTION,
  REASON,
  TIER,
  count,
  dotDate,
  eok,
  label,
  marks,
  pct,
  pp,
  reportCounts,
  sameIndustry,
  share,
  tone,
  windowLabel,
} from "./labels";

const COLUMNS = 10;

// Report counts of one company (spec §7): label, the three counts and the last stock report.
export function ReportsCell({ coverage }) {
  return (
    <td>
      <span className={`badge peer-coverage ${coverage?.label || ""}`}>
        {label(COVERAGE, coverage?.label)}
      </span>
      <small className="peer-sub">{reportCounts(coverage)}</small>
      {coverage?.last_stock_report_date && (
        <small className="peer-sub">
          마지막 종목 리포트 {dotDate(coverage.last_stock_report_date)}
        </small>
      )}
    </td>
  );
}

// The chosen window's simple and market-relative returns, always shown together (spec §9).
export function ReturnsCell({ price, period }) {
  const simple = price?.returns?.[period],
    excess = price?.excess?.[period];
  return (
    <td className="num">
      <strong className={tone(simple)}>{pct(simple)}</strong>
      <small className={`peer-sub ${tone(excess)}`}>{pp(excess)}</small>
    </td>
  );
}

export function PeriodHeader({ period }) {
  return (
    <th className="num">
      {windowLabel(period)} 수익률
      <small>단순 · 시장 대비</small>
    </th>
  );
}

function side(name, match) {
  if (!match) return `${name} 유사도: 해당 없음`;
  const percentile =
    match.percentile == null
      ? ""
      : ` · 백분위 ${Number(match.percentile).toFixed(1)}`;
  return `${name} 유사도: ${label(TIER, match.tier)}${percentile}`;
}

function Business({ heading, oneLine, segments, isActive }) {
  return (
    <section>
      <h4>{heading}</h4>
      <p>{oneLine || "한 줄 요약이 없습니다."}</p>
      {segments?.length ? (
        <ul>
          {segments.map((s, i) => (
            <li key={s.no ?? i} className={isActive(s) ? "active" : ""}>
              <span>{s.name || "이름 없는 부문"}</span>
              <small>{share(s.revenue_share_pct)}</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="peer-none">사업부문 정보가 없습니다.</p>
      )}
    </section>
  );
}

// The peers list (spec §12.5): candidate rows stand out, other rows show why they are not
// candidates; a row opens that company, its toggle shows the two businesses side by side.
export default function PeerTable({ peers, seed, chosen, period, onStock }) {
  const [open, setOpen] = useState([]);
  const toggle = (code) =>
    setOpen((s) =>
      s.includes(code) ? s.filter((c) => c !== code) : [...s, code],
    );
  const profile = seed?.profile || {};
  return (
    <div className="peer-table-wrap">
      <table className="peer-table">
        <thead>
          <tr>
            <th className="peer-rank">순위</th>
            <th>회사</th>
            <th>유사도 · 겹치는 부문</th>
            <th>공통 키워드 · 산업분류</th>
            <th>리포트 · 최근 1년</th>
            <PeriodHeader period={period} />
            <th>반응</th>
            <th className="num">시총</th>
            <th className="num">
              거래대금
              <small>20일 평균</small>
            </th>
            <th className="peer-toggle-col">
              <span className="sr-only">사업 비교</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {peers.map((p) => {
            const expanded = open.includes(p.code);
            const reasons = p.not_candidate_reasons || [];
            const name = p.name || p.code;
            return (
              <React.Fragment key={p.code}>
                <tr
                  className={`peer-row ${p.candidate ? "candidate" : ""}`}
                  onClick={() => onStock(p.code)}
                >
                  <td className="peer-rank">{p.rank ?? "—"}</td>
                  <td className="peer-company">
                    <button
                      type="button"
                      className="peer-name"
                      onClick={(e) => {
                        e.stopPropagation();
                        onStock(p.code);
                      }}
                    >
                      {name}
                    </button>
                    <small>
                      {p.code} · {p.market || "시장 미상"}
                    </small>
                    <div className="peer-marks">
                      {p.candidate && (
                        <span className="badge peer-candidate">후보</span>
                      )}
                      {marks(p).map((m) => (
                        <span className="badge peer-mark" key={m}>
                          {m}
                        </span>
                      ))}
                    </div>
                    {!p.candidate && reasons.length > 0 && (
                      <p className="peer-reasons">
                        {reasons.map((r) => label(REASON, r)).join(" · ")}
                      </p>
                    )}
                  </td>
                  <td
                    title={`${side("회사", p.company_match)}\n${side("부문", p.segment_match)}`}
                  >
                    <span className={`badge peer-tier ${p.tier || ""}`}>
                      {label(TIER, p.tier)}
                    </span>
                    <small className="peer-sub">
                      {p.segment_match
                        ? `${p.segment_match.segment || "이름 없는 부문"} · ${share(p.segment_match.revenue_share_pct)}`
                        : "회사 전체 기준"}
                    </small>
                  </td>
                  <td>
                    {p.shared_terms?.length ? (
                      <div className="peer-terms">
                        {p.shared_terms.map((t, i) => (
                          <span
                            key={`${t.term}-${i}`}
                            title={`이 용어를 가진 회사 ${count(t.companies)}곳`}
                          >
                            {t.term} <small>({count(t.companies)}곳)</small>
                          </span>
                        ))}
                      </div>
                    ) : (
                      <span className="peer-none">공통 키워드 없음</span>
                    )}
                    <small className="peer-sub">
                      산업분류 {sameIndustry(p.same_industry)}
                      {p.sector_minor ? ` · ${p.sector_minor}` : ""}
                    </small>
                  </td>
                  <ReportsCell coverage={p.coverage} />
                  <ReturnsCell price={p.price} period={period} />
                  <td>
                    <span className={`peer-reaction ${p.reaction || ""}`}>
                      {label(REACTION, p.reaction)}
                    </span>
                  </td>
                  <td className="num">{eok(p.price?.market_cap)}</td>
                  <td className="num">{eok(p.price?.avg_value_20d)}</td>
                  <td className="peer-toggle-col">
                    <button
                      type="button"
                      className="icon-button"
                      aria-expanded={expanded}
                      aria-label={`${name} 사업 비교 ${expanded ? "접기" : "펼치기"}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        toggle(p.code);
                      }}
                    >
                      {expanded ? (
                        <ChevronUp size={16} />
                      ) : (
                        <ChevronDown size={16} />
                      )}
                    </button>
                  </td>
                </tr>
                {expanded && (
                  <tr className="peer-detail">
                    <td colSpan={COLUMNS}>
                      <div className="peer-compare">
                        <Business
                          heading={`기준 회사 · ${seed?.name || "—"}`}
                          oneLine={profile.niche_industry || profile.summary}
                          segments={profile.segments}
                          isActive={(s) => chosen != null && s.no === chosen.no}
                        />
                        <Business
                          heading={name}
                          oneLine={p.one_line}
                          segments={p.segments}
                          isActive={(s) =>
                            !!p.segment_match?.segment &&
                            s.name === p.segment_match.segment
                          }
                        />
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
