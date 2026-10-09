import React, { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "../api";
import "./freshness.css";

// "2026-10-08" or "2026-10-08T09:12:00+09:00" (already Korean time) → "10월 8일".
function monthDay(value) {
  const found = /^\d{4}-(\d{2})-(\d{2})/.exec(value || "");
  return found ? `${Number(found[1])}월 ${Number(found[2])}일` : null;
}

// Reports have no note of their own (spec §12.3), so the screen says why they are stale.
function reportNote(reports) {
  const day = monthDay(reports.latest_at);
  return day
    ? `리포트가 ${day} 이후 들어오지 않았습니다`
    : "리포트 자료가 없습니다";
}

// The status line on top of every screen (spec §12.5): GET /api/freshness when the app loads and
// again on the re-check button. Red with the reasons when either side is stale; grey when the
// address fails (its sentence stays in the tooltip).
export default function StatusLine() {
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [checking, setChecking] = useState(true),
    [check, setCheck] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setChecking(true);
    api("/freshness", { signal: controller.signal })
      .then((x) => {
        setData(x);
        setError("");
        setChecking(false);
      })
      .catch((e) => {
        if (e.name !== "AbortError") {
          setData(null);
          setError(e.message);
          setChecking(false);
        }
      });
    return () => controller.abort();
  }, [check]);
  const prices = data?.prices,
    reports = data?.reports;
  const notes = [];
  if (prices?.stale) notes.push(prices.note || "주가가 최신이 아닙니다");
  if (reports?.stale) notes.push(reportNote(reports));
  const state = !data ? "unknown" : notes.length ? "stale" : "fresh";
  return (
    <div className={`status-line ${state}`} role="status">
      {data ? (
        <>
          <span>
            주가 {monthDay(prices?.as_of) || "기록 없음"} · 리포트{" "}
            {monthDay(reports?.latest_at) || "기록 없음"} 기준
          </span>
          {notes.map((note) => (
            <span className="status-note" key={note}>
              {note}
            </span>
          ))}
        </>
      ) : (
        <span title={error || undefined}>
          {error ? "자료 기준일 확인 불가" : "자료 기준일 확인 중…"}
        </span>
      )}
      <button
        type="button"
        className="status-recheck"
        aria-label="자료 기준일 다시 확인"
        title="다시 확인"
        disabled={checking}
        onClick={() => setCheck((x) => x + 1)}
      >
        <RefreshCw size={12} className={checking ? "spin" : ""} />
      </button>
    </div>
  );
}
