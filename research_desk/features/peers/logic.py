"""Peers calculations: no DB, no network, no settings, no files.

- Comparison keys (spec §5.4): squash a term (lower case; drop spaces, middle dots and hyphens),
  then turn every synonym spelling into its standard spelling. ``Legacy DRAM``, ``legacy-dram``
  and ``Legacy 디램`` all become ``legacydram``. Grounding, shared terms, the term table and theme
  search all compare these keys. A term's display is the synonym table's standard spelling, else
  the first original spelling met in the build.
- Input assembly (§5.1): labelled blocks from the business-report sections, each cut at its cap,
  the whole input at ``INPUT_CAP``; ``truncated`` says whether anything was cut.
- Grounding and caps (§5.2, §5.4): terms missing from the input are dropped and counted; then
  lists are de-duplicated by key and cut to their caps.
- Embedding texts (§6.1), unit vectors, the seed segment order (§6.2).
- Percentile tables, interpolation and tiers (§6.3): the web reads a similarity's percentile from
  a build's table with ``percentile_of`` and its tier with ``tier_of``.
- Build status (95 %), the six-hour rule for a running build, and retention (§6.5).

The numbers are adjustable defaults, kept here as constants.
"""
from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

from .schemas import CompanyProfile, Segment

# ── comparison keys (§5.4) ───────────────────────────────────────────────────

# Middle dots (the spec's ·, plus the look-alikes Korean documents use) and hyphens.
MIDDLE_DOTS = "·ㆍ・‧∙⋅"
HYPHENS = "-‐‑‒–−﹣－"
_SQUASH = re.compile(rf"[\s{re.escape(MIDDLE_DOTS + HYPHENS)}]+")


def squash(text: str) -> str:
    """Lower case without spaces, middle dots or hyphens."""
    return _SQUASH.sub("", (text or "").lower())


@dataclass(frozen=True)
class Synonyms:
    """The synonym table, ready for keys: ``key(text)`` and ``display(key)``.

    ``displays``: standard key → standard spelling. ``variants``: squashed other spelling →
    standard key. ``fingerprint``: SHA-256 of the table's content (the build records it).
    """
    displays: Mapping[str, str]
    variants: Mapping[str, str]
    fingerprint: str
    pattern: Optional[re.Pattern] = None

    def key(self, text: str) -> str:
        squashed = squash(text)
        if self.pattern is None or not squashed:
            return squashed
        return self.pattern.sub(lambda match: self.variants[match.group(0)], squashed)

    def display(self, key: str) -> Optional[str]:
        return self.displays.get(key)


def parse_synonyms(mapping: Optional[Mapping[str, Sequence[str]]]) -> Synonyms:
    """A Synonyms table from ``{standard spelling: [other spellings]}``.

    ValueError for a malformed or ambiguous table: an entry that is not a list of text, an empty
    spelling, one spelling under two standards, or a spelling that is another entry's standard.
    """
    mapping = mapping or {}
    if not isinstance(mapping, Mapping):
        raise ValueError("the synonym table must map a standard spelling to a list of spellings")
    displays: dict[str, str] = {}
    for standard in mapping:
        if not isinstance(standard, str) or not squash(standard):
            raise ValueError(f"bad standard spelling: {standard!r}")
        key = squash(standard)
        if key in displays:
            raise ValueError(f"two entries for one standard spelling: {standard!r}")
        displays[key] = standard.strip()
    variants: dict[str, str] = {}
    for standard, others in mapping.items():
        if not isinstance(others, (list, tuple)):
            raise ValueError(f"the spellings of {standard!r} must be a list")
        for other in others:
            if not isinstance(other, str) or not squash(other):
                raise ValueError(f"bad spelling under {standard!r}: {other!r}")
            variant = squash(other)
            if variant == squash(standard):
                continue
            if variant in displays or variants.get(variant, squash(standard)) != squash(standard):
                raise ValueError(f"the spelling {other!r} belongs to more than one standard")
            variants[variant] = squash(standard)
    canonical = {standard.strip(): sorted({str(o).strip() for o in others})
                 for standard, others in mapping.items()}
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    pattern = None
    if variants:
        # Longest first, so a longer spelling wins over a shorter one inside it.
        pattern = re.compile("|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True)))
    return Synonyms(displays=displays, variants=variants, fingerprint=fingerprint, pattern=pattern)


def comparison_key(text: str, synonyms: Synonyms) -> str:
    """The comparison key of ``text`` (spec §5.4)."""
    return synonyms.key(text)


def term_keys(terms: Iterable[str], synonyms: Synonyms) -> list[str]:
    """The keys of ``terms``: each once, in order, empty keys left out."""
    keys: dict[str, None] = {}
    for term in terms:
        key = synonyms.key(term)
        if key:
            keys.setdefault(key, None)
    return list(keys)


def _profile_raw_terms(profile: Mapping[str, Any]) -> list[str]:
    """Keywords, products, then each segment's keywords and products, as written."""
    raw = list(profile.get("keywords") or []) + list(profile.get("products") or [])
    for segment in profile.get("segments") or []:
        raw += list(segment.get("keywords") or []) + list(segment.get("products") or [])
    return raw


def profile_terms(profile: Mapping[str, Any], synonyms: Synonyms) -> list[str]:
    """The profile's ``terms`` column: keys of its keywords and products and its segments'."""
    return term_keys(_profile_raw_terms(profile), synonyms)


def term_table(profiles: Iterable[Mapping[str, Any]], synonyms: Synonyms) -> dict[str, dict]:
    """``{key: {"display": …, "companies": n}}`` over ``profiles`` (in the order given).

    ``companies`` counts the profiles holding the key; ``display`` is the standard spelling, else
    the first original spelling met.
    """
    table: dict[str, dict] = {}
    for profile in profiles:
        seen: set[str] = set()
        for raw in _profile_raw_terms(profile):
            key = synonyms.key(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            entry = table.setdefault(key, {"display": synonyms.display(key) or raw.strip(), "companies": 0})
            entry["companies"] += 1
    return table


# ── input assembly (§5.1) ────────────────────────────────────────────────────

OVERVIEW, PRODUCTS, SALES, OTHER_NOTES, FINANCIAL = "020100", "020200", "020400", "020700", "020800"
INPUT_SECTIONS: tuple[str, ...] = (OVERVIEW, PRODUCTS, SALES, OTHER_NOTES, FINANCIAL)

OVERVIEW_CAP = 6000
PRODUCTS_CAP = 2500
PRODUCT_TABLES_CAP = 2500
SHORT_OVERVIEW = 800          # below this, other notes and sales are added
OTHER_NOTES_CAP = 3000
SALES_CAP = 1500
FINANCIAL_CAP = 6000
INPUT_CAP = 12000

LABELS = {
    "overview": "[사업의 개요]",
    "products": "[주요 제품 및 서비스]",
    "product_tables": "[주요 제품 표]",
    "other_notes": "[기타 참고사항]",
    "sales": "[매출 및 수주]",
    "financial": "[영업의 현황]",
}

PRODUCT_TABLE_WORDS = ("품목", "제품", "부문", "용도", "상표")
UNIT_PRICE_MARKS = ("천/톤", "원/", "$/", "단가")
NUMERIC_CELLS_MAX_PERCENT = 70
_NUMBER = re.compile(r"^[+\-−△▲▽]?\d+(?:\.\d+)?$")
_EMPTY_CELL = re.compile(r"^[-–—]*$")


@dataclass(frozen=True)
class Section:
    """One business-report section's text: paragraphs and tables (rows ``\\n``, cells `` | ``)."""
    prose: str = ""
    tables: str = ""


@dataclass(frozen=True)
class AssembledInput:
    text: str
    sections: tuple[str, ...]     # section codes that gave text, in block order
    truncated: bool


def split_tables(table_text: str) -> list[str]:
    """The tables of a section: separated by blank lines."""
    return [t.strip() for t in re.split(r"\n\s*\n", table_text or "") if t.strip()]


def _cells(table: str) -> list[list[str]]:
    return [[cell.strip() for cell in row.split("|")] for row in table.splitlines() if row.strip()]


def _is_number(cell: str) -> bool:
    plain = cell.replace(",", "").replace("%", "").replace(" ", "")
    if plain.startswith("(") and plain.endswith(")"):
        plain = plain[1:-1]
    return bool(_NUMBER.match(plain))


def is_product_table(table: str) -> bool:
    """A products / segments table: its first row names one of ``PRODUCT_TABLE_WORDS``; it has
    more than one row, no unit price, and at most 70 % numeric cells (empty and dash cells are
    not counted)."""
    rows = _cells(table)
    if len(rows) < 2:
        return False
    if not any(word in " ".join(rows[0]) for word in PRODUCT_TABLE_WORDS):
        return False
    if any(mark in table for mark in UNIT_PRICE_MARKS):
        return False
    cells = [cell for row in rows for cell in row if not _EMPTY_CELL.match(cell)]
    numeric = sum(1 for cell in cells if _is_number(cell))
    return numeric * 100 <= NUMERIC_CELLS_MAX_PERCENT * len(cells)


def product_tables(table_text: str) -> str:
    """The section's product and segment tables, joined by blank lines."""
    return "\n\n".join(t for t in split_tables(table_text) if is_product_table(t))


def _join(*parts: str) -> str:
    return "\n\n".join(part.strip() for part in parts if part and part.strip())


def assemble_input(sections: Mapping[str, Section]) -> AssembledInput:
    """The AI input for one company (spec §5.1).

    Blocks in this order, each with its label and cut at its cap: overview; products text and
    product tables (or, for the financial format — a ``020800`` section — its business section,
    text then tables); other notes and sales only when the overview is under 800 characters.
    Empty blocks are left out. The whole input is cut at ``INPUT_CAP``.
    """
    def text(code: str, part: str) -> str:
        section = sections.get(code)
        return (getattr(section, part) or "").strip() if section is not None else ""

    overview = text(OVERVIEW, "prose")
    financial = FINANCIAL in sections
    candidates: list[tuple[str, str, str, int]] = [("overview", OVERVIEW, overview, OVERVIEW_CAP)]
    if not financial:
        candidates += [("products", PRODUCTS, text(PRODUCTS, "prose"), PRODUCTS_CAP),
                       ("product_tables", PRODUCTS, product_tables(text(PRODUCTS, "tables")),
                        PRODUCT_TABLES_CAP)]
    if len(overview) < SHORT_OVERVIEW:
        candidates += [("other_notes", OTHER_NOTES, text(OTHER_NOTES, "prose"), OTHER_NOTES_CAP),
                       ("sales", SALES, text(SALES, "prose"), SALES_CAP)]
    if financial:
        candidates.append(("financial", FINANCIAL,
                           _join(text(FINANCIAL, "prose"), text(FINANCIAL, "tables")), FINANCIAL_CAP))

    blocks: list[str] = []
    used: list[str] = []
    truncated = False
    for label, code, content, cap in candidates:
        if not content:
            continue
        if len(content) > cap:
            content, truncated = content[:cap], True
        blocks.append(f"{LABELS[label]}\n{content}")
        if code not in used:
            used.append(code)
    joined = "\n\n".join(blocks)
    if len(joined) > INPUT_CAP:
        joined, truncated = joined[:INPUT_CAP], True
    return AssembledInput(text=joined, sections=tuple(used), truncated=truncated)


# ── grounding (§5.4) and caps (§5.2) ─────────────────────────────────────────

GROUNDING_MIN = 0.8

PROFILE_CAPS = {"products": 12, "keywords": 15, "applications": 8, "customers": 8, "competitors": 8}
SEGMENT_CAPS = {"products": 6, "keywords": 6}
SEGMENTS_MAX = 6
ROLES_MAX = 2
NICHE_MAX_CHARS = 40
SUMMARY_MAX_CHARS = 160

_GROUNDED_FIELDS = ("products", "keywords", "customers", "competitors")
_GROUNDED_SEGMENT_FIELDS = ("products", "keywords")


def grounding_ratio(kept: int, produced: int) -> float:
    """Kept terms over the terms the AI gave; 1.0 when it gave none."""
    return 1.0 if produced == 0 else kept / produced


def ground_profile(profile: CompanyProfile, text: str,
                   synonyms: Synonyms) -> tuple[CompanyProfile, int, int]:
    """``(profile with ungrounded terms dropped, terms kept, terms given)``.

    Products, keywords, customers, competitors and each segment's products and keywords must
    appear in ``text`` (both compared as keys). Blank terms are dropped and not counted. Other
    fields are left as they are.
    """
    haystack = synonyms.key(text)
    counts = [0, 0]

    def keep(terms: list[str]) -> list[str]:
        kept = []
        for term in terms:
            term = term.strip()
            key = synonyms.key(term)
            if not key:
                continue
            counts[1] += 1
            if key in haystack:
                counts[0] += 1
                kept.append(term)
        return kept

    segments = [segment.model_copy(update={f: keep(getattr(segment, f)) for f in _GROUNDED_SEGMENT_FIELDS})
                for segment in profile.segments]
    update: dict[str, Any] = {f: keep(getattr(profile, f)) for f in _GROUNDED_FIELDS}
    update["segments"] = segments
    return profile.model_copy(update=update), counts[0], counts[1]


def _tidy(terms: Iterable[str], cap: int, synonyms: Synonyms) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        term = term.strip()
        key = synonyms.key(term)
        if key and key not in seen:
            seen.add(key)
            out.append(term)
    return out[:cap]


def _share(value: float) -> float:
    """A share in 0..100 stays; anything else (and -1) is "unknown", -1."""
    return float(value) if 0 <= value <= 100 else -1.0


def cap_profile(profile: CompanyProfile, synonyms: Synonyms) -> CompanyProfile:
    """Lists de-duplicated by key and cut to their caps, texts to their lengths, nameless segments
    dropped, impossible shares turned into -1 (spec §5.2: limits are cut in code)."""
    segments = []
    for segment in profile.segments:
        name = segment.name.strip()
        if not name:
            continue
        segments.append(Segment(
            name=name,
            products=_tidy(segment.products, SEGMENT_CAPS["products"], synonyms),
            keywords=_tidy(segment.keywords, SEGMENT_CAPS["keywords"], synonyms),
            revenue_share_pct=_share(segment.revenue_share_pct),
        ))
    update: dict[str, Any] = {f: _tidy(getattr(profile, f), cap, synonyms) for f, cap in PROFILE_CAPS.items()}
    update.update(
        niche_industry=profile.niche_industry.strip()[:NICHE_MAX_CHARS],
        summary=profile.summary.strip()[:SUMMARY_MAX_CHARS],
        roles=list(dict.fromkeys(profile.roles))[:ROLES_MAX],
        segments=segments[:SEGMENTS_MAX],
    )
    return profile.model_copy(update=update)


# ── embedding texts (§6.1) and vectors ───────────────────────────────────────

def format_share(share: Any) -> Optional[str]:
    """``98.1`` → ``"98.1"``, ``50.0`` → ``"50"``; None for an unknown share (negative)."""
    if share is None or float(share) < 0:
        return None
    return f"{float(share):g}"


def _listed(values: Optional[Iterable[str]]) -> str:
    return ", ".join(values or [])


def company_text(profile: Mapping[str, Any]) -> str:
    """The company embedding text. Customers, competitors and group names are left out, so that
    companies do not look alike just for selling to the same buyer or belonging to one group."""
    parts = []
    for segment in profile.get("segments") or []:
        share = format_share(segment.get("revenue_share_pct"))
        head = segment.get("name", "") + (f"({share}%)" if share is not None else "")
        parts.append(f"{head}: {_listed(segment.get('products'))}")
    return (f"업종: {profile.get('niche_industry', '')}\n"
            f"요약: {profile.get('summary', '')}\n"
            f"제품: {_listed(profile.get('products'))}\n"
            f"키워드: {_listed(profile.get('keywords'))}\n"
            f"사업부문: {'; '.join(parts)}\n"
            f"적용처: {_listed(profile.get('applications'))}")


def segment_text(niche_industry: str, segment: Mapping[str, Any]) -> str:
    """One segment's embedding text: ``{niche} — {name}: {products} / {keywords}``."""
    return (f"{niche_industry} — {segment.get('name', '')}: "
            f"{_listed(segment.get('products'))} / {_listed(segment.get('keywords'))}")


def normalize(vector: Sequence[float]) -> list[float]:
    """``vector`` scaled to length 1. ValueError for a zero vector."""
    array = np.asarray(vector, dtype=float)
    norm = float(np.linalg.norm(array))
    if norm == 0 or not np.isfinite(norm):
        raise ValueError("cannot scale a zero or non-finite vector to length 1")
    return (array / norm).tolist()


# ── seed segment order (§6.2) ────────────────────────────────────────────────

def segment_order(shares: Sequence[float]) -> list[int]:
    """Segment indexes by revenue share, largest first; unknown shares (negative) after the known
    ones; equal shares keep their original order. The first is the seed's default segment."""
    return sorted(range(len(shares)),
                  key=lambda i: (shares[i] is None or shares[i] < 0,
                                 0.0 if shares[i] is None or shares[i] < 0 else -shares[i]))


# ── percentile tables, interpolation, tiers (§6.3) ───────────────────────────

PERCENTILES: tuple[float, ...] = tuple(round(95 + i / 10, 1) for i in range(50)) + (99.95, 99.99, 100.0)
MAX_PAIRS = 2_000_000
# Pairs drawn per step when sampling: two 4096 x 1536 float32 gathers (about 50 MB) at a time.
_SAMPLE_CHUNK = 4096

TIER_VERY_HIGH = 99.5
TIER_HIGH = 98.0
TIER_RELATED = 95.0


def _unit_rows(vectors) -> np.ndarray:
    array = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def _all_pairs(units: np.ndarray, groups: Optional[np.ndarray]) -> np.ndarray:
    sims = []
    for i in range(len(units) - 1):
        row = units[i + 1:] @ units[i]
        if groups is not None:
            row = row[groups[i + 1:] != groups[i]]
        sims.append(row)
    return np.concatenate(sims) if sims else np.empty(0)


def _sampled_pairs(units: np.ndarray, groups: Optional[np.ndarray], size: int, seed: int) -> np.ndarray:
    """``size`` cosines of random pairs (two different vectors, of different groups)."""
    rng = np.random.default_rng(seed)
    n = len(units)
    compact = units.astype(np.float32)
    out = np.empty(size, dtype=float)
    have = 0
    while have < size:
        i = rng.integers(0, n, size=_SAMPLE_CHUNK)
        j = rng.integers(0, n, size=_SAMPLE_CHUNK)
        keep = i != j
        if groups is not None:
            keep &= groups[i] != groups[j]
        i, j = i[keep][:size - have], j[keep][:size - have]
        out[have:have + len(i)] = np.einsum("ij,ij->i", compact[i], compact[j])
        have += len(i)
    return out


def _percentile_values(sims: np.ndarray) -> list[float]:
    return [float(v) for v in np.percentile(sims, PERCENTILES)]


def quantile_table(vectors, groups: Optional[Sequence[Any]] = None, *, max_pairs: int = MAX_PAIRS,
                   seed: int = 0) -> Optional[list[list[float]]]:
    """``[[percentile, cosine], …]`` over the cosines of all pairs of ``vectors`` (spec §6.3).

    A vector is never paired with itself; with ``groups`` (one label per vector, e.g. the
    company of each segment) two vectors of one group are not paired either. Over ``max_pairs``
    pairs, ``max_pairs`` random pairs (fixed ``seed``) stand in for them all. None without a pair.
    """
    units = _unit_rows(vectors) if len(vectors) else np.empty((0, 0))
    labels = np.asarray(groups) if groups is not None else None
    n = len(units)
    pairs = n * (n - 1) // 2
    if labels is not None:
        _, counts = np.unique(labels, return_counts=True)
        pairs -= int(sum(c * (c - 1) // 2 for c in counts))
    if pairs <= 0:
        return None
    sims = (_all_pairs(units, labels) if pairs <= max_pairs
            else _sampled_pairs(units, labels, max_pairs, seed))
    return [[p, c] for p, c in zip(PERCENTILES, _percentile_values(sims))]


def percentile_of(similarity: float, table: Optional[Sequence[Sequence[float]]]) -> Optional[float]:
    """The percentile of ``similarity`` in a build's table, interpolated on a straight line
    between the two neighbouring points. None below the 95th value (not listed); 100 above the
    100th value. Where several points share one cosine, the highest percentile counts."""
    if not table:
        return None
    points = sorted((float(p), float(c)) for p, c in table)
    cosines = [c for _, c in points]
    if similarity < cosines[0]:
        return None
    k = bisect_right(cosines, similarity) - 1
    if k >= len(points) - 1:
        return points[-1][0]
    (p0, c0), (p1, c1) = points[k], points[k + 1]
    return p0 + (similarity - c0) * (p1 - p0) / (c1 - c0)


def tier_of(percentile: Optional[float]) -> Optional[str]:
    """``very_high`` from 99.5, ``high`` from 98, ``related`` from 95, else None."""
    if percentile is None:
        return None
    if percentile >= TIER_VERY_HIGH:
        return "very_high"
    if percentile >= TIER_HIGH:
        return "high"
    if percentile >= TIER_RELATED:
        return "related"
    return None


# ── pilot neighbours ─────────────────────────────────────────────────────────

def top_companies(seed: str, seed_vector, codes: Sequence[str], matrix, k: int = 10) -> list[tuple[str, float]]:
    """The ``k`` companies most like ``seed`` (rows of ``matrix``, one per code), seed left out."""
    sims = _unit_rows(matrix) @ np.asarray(normalize(seed_vector))
    ranked = sorted(((code, float(s)) for code, s in zip(codes, sims) if code != seed),
                    key=lambda item: (-item[1], item[0]))
    return ranked[:k]


def top_segments(seed: str, seed_vector, codes: Sequence[str], seg_nos: Sequence[int], matrix,
                 k: int = 10) -> list[tuple[str, int, float]]:
    """The ``k`` other companies whose best segment is most like ``seed_vector``."""
    sims = _unit_rows(matrix) @ np.asarray(normalize(seed_vector))
    best: dict[str, tuple[int, float]] = {}
    for code, seg_no, s in zip(codes, seg_nos, sims):
        if code != seed and (code not in best or s > best[code][1]):
            best[code] = (seg_no, float(s))
    ranked = sorted(((code, no, s) for code, (no, s) in best.items()), key=lambda item: (-item[2], item[0]))
    return ranked[:k]


# ── build status, the six-hour rule, retention (§6.5) ────────────────────────

DONE_PERCENT = 95
STALE_AFTER = timedelta(hours=6)
KEEP_DONE_BUILDS = 3


def build_status(eligible: int, profiled: int, *, pilot: bool) -> str:
    """``pilot`` for a pilot; ``done`` when profiled ≥ 95 % of eligible; else ``incomplete``
    (also with no eligible company: an empty build is never published)."""
    if pilot:
        return "pilot"
    if eligible > 0 and profiled * 100 >= eligible * DONE_PERCENT:
        return "done"
    return "incomplete"


def is_stale(heartbeat_at: datetime, now: datetime) -> bool:
    """A running build is stale after six hours without progress (exactly six is not stale)."""
    return now - heartbeat_at > STALE_AFTER


@dataclass(frozen=True)
class RetentionPlan:
    delete_build_ids: tuple[int, ...]
    delete_profile_keys: tuple[tuple[int, str], ...]           # (fiscal_year, profile_version)
    delete_embedding_keys: tuple[tuple[int, str, str], ...]    # (fiscal_year, profile_version, model)


def retention_plan(builds: Sequence[Mapping[str, Any]], profile_keys: Iterable[tuple[int, str]],
                   embedding_keys: Iterable[tuple[int, str, str]],
                   current_profile_version: str) -> RetentionPlan:
    """What to delete: public (``done``) builds after the three latest (by finished_at, then id),
    and the profile and embedding rows that neither a remaining build nor the current profile
    version uses."""
    done = sorted((b for b in builds if b.get("status") == "done"),
                  key=lambda b: (b.get("finished_at") or "", b["build_id"]), reverse=True)
    dropped = {b["build_id"] for b in done[KEEP_DONE_BUILDS:]}
    remaining = [b for b in builds if b["build_id"] not in dropped]
    used_profiles = {(b["fiscal_year"], b["profile_version"]) for b in remaining}
    used_embeddings = {(b["fiscal_year"], b["profile_version"], b["embed_model"]) for b in remaining}
    profiles = sorted(k for k in set(profile_keys)
                      if k not in used_profiles and k[1] != current_profile_version)
    embeddings = sorted(k for k in set(embedding_keys)
                        if k not in used_embeddings and k[1] != current_profile_version)
    return RetentionPlan(tuple(sorted(dropped)), tuple(profiles), tuple(embeddings))
