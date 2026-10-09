// The peers screens' one place for turning API codes into Korean labels and numbers into Korean
// formats (spec §12.5: 억 원, % and %p with one decimal). Any value may be null; unknown shows "—".

export const WINDOWS = [
  ["1w", "1주"],
  ["1m", "1개월"],
  ["3m", "3개월"],
];
export const DEFAULT_WINDOW = "1m";

export const TIER = { very_high: "매우 유사", high: "유사", related: "관련" };
export const REACTION = {
  none: "미반응",
  partial: "부분반응",
  reacted: "반응",
  undetermined: "판단 불가",
};
export const COVERAGE = { none: "리포트 없음", few: "적음", covered: "있음" };
export const REASON = {
  has_reports: "리포트가 있음",
  reacted: "이미 반응",
  undetermined: "판정 불가",
  not_traded: "거래정지·자료 없음",
  low_liquidity: "거래대금 부족",
  holding: "지주회사",
  weak_similarity: "유사도 약함",
};
export const FLAG = {
  halted: "거래정지",
  admin_issue: "관리종목 등",
  short_history: "기간 부족",
  no_data: "자료 없음",
};
const WINDOW_LABEL = Object.fromEntries(WINDOWS);

// A code without a label shows as itself, a missing one as "—".
export const label = (map, code) =>
  code == null
    ? "—"
    : Object.prototype.hasOwnProperty.call(map, code)
      ? map[code]
      : String(code);
export const windowLabel = (code) => label(WINDOW_LABEL, code);

// Marks next to a company name; they never hide the company.
export function marks(company) {
  const out = [];
  if (company?.is_holding) out.push("지주사");
  if (company?.is_financial) out.push("금융사");
  if (company?.info_quality === "부족") out.push("정보 부족");
  for (const flag of company?.price?.flags || []) out.push(label(FLAG, flag));
  return [...new Set(out)];
}

const known = (value) =>
  value != null && value !== "" && Number.isFinite(Number(value));
const fixed = (value, digits) =>
  Number(value).toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
// One decimal; -0 becomes 0 so it shows without a sign.
const tenth = (value) => Math.round(Number(value) * 10) / 10 || 0;

function signed(value, unit) {
  if (!known(value)) return "—";
  const v = tenth(value);
  return `${v > 0 ? "+" : ""}${fixed(v, 1)}${unit}`;
}
export const pct = (value) => signed(value, "%");
export const pp = (value) => signed(value, "%p");
// "up" / "down" colour class of a return as shown (one decimal).
export const tone = (value) =>
  !known(value) || tenth(value) === 0 ? "" : tenth(value) > 0 ? "up" : "down";

// Whole won → 억: one decimal under 100억 (4.6억 stays apart from a 5억 line), whole above.
export function eok(won) {
  if (!known(won)) return "—";
  const v = Number(won) / 1e8;
  return `${fixed(v, Math.abs(v) < 100 ? 1 : 0)}억`;
}

// A revenue share in percent; -1 (or nothing) means the report gave none.
export const share = (value) =>
  !known(value) || Number(value) < 0 ? "비중 미상" : `${fixed(value, 1)}%`;

export const count = (value) =>
  known(value) ? Number(value).toLocaleString("ko-KR") : "—";
export const dotDate = (value) =>
  value ? String(value).slice(0, 10).replaceAll("-", ".") : "—";
export const sameIndustry = (value) =>
  value === true ? "같음" : value === false ? "다름" : "미상";

export const reportCounts = (coverage) =>
  coverage
    ? `종목 ${count(coverage.stock_reports)} · 섹터 ${count(coverage.sector_mentions)} · 기타 ${count(coverage.other_research)}`
    : "—";

export const basisText = (basis) =>
  basis
    ? `FY${basis.fiscal_year ?? "—"} 사업보고서 · ${count(basis.companies_profiled)}/${count(basis.companies_eligible)}곳`
    : "—";
