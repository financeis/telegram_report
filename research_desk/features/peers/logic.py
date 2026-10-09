"""Peers calculations: no DB, no network, no settings, no files.

- Comparison keys (spec §5.4): a whole term that is one of the synonym table's spellings becomes
  its standard spelling; otherwise each word of the term (words part at spaces, middle dots,
  hyphens and slashes) that is a whole spelling does. Then case, spaces, middle dots and hyphens
  are ignored (``squash``; terms, words and spellings are compared squashed). ``디램``, ``D램``
  and ``DRAM`` all become ``dram``; ``Legacy DRAM``, ``legacy-dram``, ``Legacy 디램`` and
  ``LEGACY D램`` become ``legacydram``; ``D램 모듈`` and ``디램 모듈`` become ``dram모듈``. A
  spelling inside a longer word is never replaced: ``시디램프`` stays ``시디램프``. Shared terms,
  the term table and theme search compare these keys. Grounding looks in the squashed input text
  (nothing replaced there) for any spelling of the term: as written, any spelling of its synonym
  entry when the whole term is one, or the term with its words swapped for other spellings of
  theirs (at most ``SPELLINGS_MAX``). A term's display is the synonym table's standard spelling,
  else the first original spelling met in the build.
- Input assembly (§5.1): labelled blocks from the business-report sections, each cut at its cap,
  the whole input at ``INPUT_CAP``; ``truncated`` says whether anything was cut.
- Grounding and caps (§5.2, §5.4): terms missing from the input are dropped and counted; then
  lists are de-duplicated by key and cut to their caps, segments in revenue-share order first.
- Embedding texts (§6.1), unit vectors, the seed segment order (§6.2).
- Percentile tables, interpolation and tiers (§6.3): the web reads a similarity's percentile from
  a build's table with ``percentile_of`` and its tier with ``tier_of``.
- Build status (95 %), the six-hour rule for a running build, and retention (§6.5): the three
  latest public builds, newer unpublished ones and running ones stay.
- The web side (§6.2–§6.4, §9, §10, §12.2): each company's best segment, one side's top matches,
  grading, merging and ranking the peers list, shared terms, the same industry, the price
  reaction, the candidate flag, theme search's normalized query, query terms, term ranking and
  reciprocal rank fusion (RRF).

The numbers are adjustable defaults, kept here as constants (the seed's 10 %p threshold and the
six candidate conditions are the user's own choices).
"""
from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import islice, product
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import numpy as np

from .schemas import CompanyProfile, Segment

# ── comparison keys (§5.4) ───────────────────────────────────────────────────

# Middle dots (the spec's ·, plus the look-alikes Korean documents use), hyphens and slashes.
MIDDLE_DOTS = "·ㆍ・‧∙⋅"
HYPHENS = "-‐‑‒–−﹣－"
SLASHES = "/／"
_SQUASH = re.compile(rf"[\s{re.escape(MIDDLE_DOTS + HYPHENS)}]+")
# A term's words part at spaces, middle dots, hyphens and slashes; the split keeps the separators
# (odd items), so a slash stays in the key as before.
_WORDS = re.compile(rf"([\s{re.escape(MIDDLE_DOTS + HYPHENS + SLASHES)}]+)")
# The most spellings grounding looks for one term: its words' spellings multiply.
SPELLINGS_MAX = 64


def squash(text: str) -> str:
    """Lower case without spaces, middle dots or hyphens."""
    return _SQUASH.sub("", (text or "").lower())


@dataclass(frozen=True)
class Synonyms:
    """The synonym table, ready for keys: ``key(term)``, ``spellings(term)`` and ``display(key)``.

    ``displays``: standard key → standard spelling. ``variants``: squashed other spelling →
    standard key. ``forms``: standard key → every squashed spelling of its entry, the standard
    first. ``fingerprint``: SHA-256 of the table's content (the build records it).
    """
    displays: Mapping[str, str]
    variants: Mapping[str, str]
    forms: Mapping[str, tuple[str, ...]]
    fingerprint: str

    def _standard(self, spelling: str) -> Optional[str]:
        """The standard key of the entry ``spelling`` (squashed) is a spelling of, else None."""
        squashed = squash(spelling)
        return squashed if squashed in self.displays else self.variants.get(squashed)

    def key(self, term: str) -> str:
        """The comparison key of ``term``: the standard key when the whole term is a spelling of
        an entry; otherwise the term squashed after each word that is a whole spelling becomes
        its standard key. Nothing inside a word is replaced."""
        whole = self._standard(term)
        if whole is not None:
            return whole
        parts = _WORDS.split(term or "")
        parts[::2] = [self._standard(word) or word for word in parts[::2]]
        return squash("".join(parts))

    def spellings(self, term: str) -> tuple[str, ...]:
        """The squashed spellings that stand for ``term`` in a squashed text, as written first:
        every spelling of its entry when the whole term is a spelling, and the term with each
        word that is a whole spelling swapped for the other spellings of that word's entry. At
        most ``SPELLINGS_MAX``; none for a blank term."""
        found = dict.fromkeys([squash(term)])
        whole = self._standard(term)
        if whole is not None:
            found.update(dict.fromkeys(self.forms[whole]))
        choices: list[tuple[str, ...]] = []
        for index, part in enumerate(_WORDS.split(term or "")):
            standard = self._standard(part) if index % 2 == 0 else None
            if standard is None:
                choices.append((part,))
            else:
                word = squash(part)
                choices.append((word,) + tuple(f for f in self.forms[standard] if f != word))
        for combination in islice(product(*choices), SPELLINGS_MAX):
            found.setdefault(squash("".join(combination)), None)
        return tuple(spelling for spelling in found if spelling)[:SPELLINGS_MAX]

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
    forms: dict[str, list[str]] = {key: [key] for key in displays}
    for variant, key in variants.items():
        forms[key].append(variant)
    canonical = {standard.strip(): sorted({str(o).strip() for o in others})
                 for standard, others in mapping.items()}
    fingerprint = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return Synonyms(displays=displays, variants=variants,
                    forms={key: tuple(spellings) for key, spellings in forms.items()},
                    fingerprint=fingerprint)


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
NO_ROLE = "기타"              # stored when the AI gives no role (spec §5.2: 1 to 2 roles)
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
    appear in ``text``: one of the term's spellings (``Synonyms.spellings``: as written, its
    synonym entry's, or with its words swapped for other spellings of theirs) inside the squashed
    text (case and spacing ignored, nothing replaced). Blank terms are dropped and not counted.
    Other fields are left as they are.
    """
    haystack = squash(text)
    counts = [0, 0]

    def keep(terms: list[str]) -> list[str]:
        kept = []
        for term in terms:
            term = term.strip()
            key = synonyms.key(term)
            if not key:
                continue
            counts[1] += 1
            if any(spelling in haystack for spelling in synonyms.spellings(term)):
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
    dropped, impossible shares turned into -1 (spec §5.2: limits are cut in code). Segments are
    put in revenue-share order (largest first, unknown after the known, ties as given) before
    the first ``SEGMENTS_MAX`` are kept; no role at all becomes ``[NO_ROLE]``."""
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
    by_share = [segments[i] for i in segment_order([s.revenue_share_pct for s in segments])]
    update: dict[str, Any] = {f: _tidy(getattr(profile, f), cap, synonyms) for f, cap in PROFILE_CAPS.items()}
    update.update(
        niche_industry=profile.niche_industry.strip()[:NICHE_MAX_CHARS],
        summary=profile.summary.strip()[:SUMMARY_MAX_CHARS],
        roles=list(dict.fromkeys(profile.roles))[:ROLES_MAX] or [NO_ROLE],
        segments=by_share[:SEGMENTS_MAX],
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
# Segment pairs over this many are sampled (spec §6.3); company pairs never are.
MAX_PAIRS = 2_000_000
# Pairs drawn per step when sampling: two 4096 x 1536 float32 gathers (about 50 MB) at a time.
_SAMPLE_CHUNK = 4096

TIER_VERY_HIGH = 99.5
TIER_HIGH = 98.0
TIER_RELATED = 95.0
TIERS: tuple[str, ...] = ("very_high", "high", "related")


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


def quantile_table(vectors, groups: Optional[Sequence[Any]] = None, *, max_pairs: Optional[int] = None,
                   seed: int = 0) -> Optional[list[list[float]]]:
    """``[[percentile, cosine], …]`` over the cosines of all pairs of ``vectors`` (spec §6.3).

    A vector is never paired with itself; with ``groups`` (one label per vector, e.g. the
    company of each segment) two vectors of one group are not paired either. Every pair counts
    unless ``max_pairs`` is given: over that many pairs, ``max_pairs`` random pairs (fixed
    ``seed``) stand in for them all. The build counts every company pair (about 3.1 million for
    2,500 companies) and samples only the segment table, at ``MAX_PAIRS``. None without a pair.
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
    sims = (_sampled_pairs(units, labels, max_pairs, seed) if max_pairs is not None and pairs > max_pairs
            else _all_pairs(units, labels))
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
# Closed builds that are never published: retention deletes those older than the public ones.
UNPUBLISHED_STATUSES = ("pilot", "failed", "incomplete")


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
    """What to delete (spec §6.5): public (``done``) builds after the three latest (by
    finished_at, then id); ``pilot``, ``failed`` and ``incomplete`` builds older than the oldest
    public build kept (a lower build_id: builds run one at a time); then the profile and
    embedding rows that neither a remaining build nor the current profile version uses. A
    ``running`` build is never deleted, nor is any build while none is public."""
    done = sorted((b for b in builds if b.get("status") == "done"),
                  key=lambda b: (b.get("finished_at") or "", b["build_id"]), reverse=True)
    kept = done[:KEEP_DONE_BUILDS]
    dropped = {b["build_id"] for b in done[KEEP_DONE_BUILDS:]}
    if kept:
        oldest_kept = min(b["build_id"] for b in kept)
        dropped |= {b["build_id"] for b in builds
                    if b.get("status") in UNPUBLISHED_STATUSES and b["build_id"] < oldest_kept}
    remaining = [b for b in builds if b["build_id"] not in dropped]
    used_profiles = {(b["fiscal_year"], b["profile_version"]) for b in remaining}
    used_embeddings = {(b["fiscal_year"], b["profile_version"], b["embed_model"]) for b in remaining}
    profiles = sorted(k for k in set(profile_keys)
                      if k not in used_profiles and k[1] != current_profile_version)
    embeddings = sorted(k for k in set(embedding_keys)
                        if k not in used_embeddings and k[1] != current_profile_version)
    return RetentionPlan(tuple(sorted(dropped)), tuple(profiles), tuple(embeddings))


# ── the peers list (§6.2–§6.4) ───────────────────────────────────────────────

COMPANY_TOP = 100        # companies by company similarity
SEGMENT_TOP = 100        # companies by their best segment's similarity to the seed's chosen segment
PEERS_MAX = 50           # the merged list (adjustable)
SHARED_TERMS_MAX = 5
# Shared-term bonus for ranking (§6.4): percentile points added per shared term. 0 = off.
SHARED_TERM_BONUS = 0.0


def ordered_segments(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Segment rows (``seg_no``, ``revenue_share_pct``) in §6.2 order: by revenue share, largest
    first, unknown shares (-1) after the known ones, equal shares by segment number. The first
    is the seed's default segment."""
    by_number = sorted(rows, key=lambda row: row["seg_no"])
    order = segment_order([row.get("revenue_share_pct") for row in by_number])
    return [by_number[i] for i in order]


def best_segments(rows: Iterable[Mapping[str, Any]]) -> list[dict]:
    """One row per company from ``match_company_segments`` rows (one per segment): its most
    similar segment (equal similarities: the lower segment number), nearest first, then by code."""
    best: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        code = row["stock_code"]
        kept = best.get(code)
        if (kept is None or row["similarity"] > kept["similarity"]
                or (row["similarity"] == kept["similarity"] and row["seg_no"] < kept["seg_no"])):
            best[code] = row
    return sorted((dict(row) for row in best.values()), key=lambda row: (-row["similarity"], row["stock_code"]))


def top_matches(rows: Iterable[Mapping[str, Any]], keep: Callable[[str], bool], k: int) -> list[dict]:
    """The ``k`` nearest rows whose code passes ``keep(code)`` (nearest first, then by code)."""
    ranked = sorted(rows, key=lambda row: (-row["similarity"], row["stock_code"]))
    return [dict(row) for row in ranked if keep(row["stock_code"])][:k]


def grade(similarity: float, table: Optional[Sequence[Sequence[float]]]) -> Optional[dict]:
    """``{"similarity", "percentile", "tier"}`` from a build's percentile table; None below the
    95th percentile value (not listed) or without a table."""
    percentile = percentile_of(similarity, table)
    tier = tier_of(percentile)
    if tier is None:
        return None
    return {"similarity": similarity, "percentile": percentile, "tier": tier}


def merge_peers(company_rows: Iterable[Mapping[str, Any]], segment_rows: Iterable[Mapping[str, Any]],
                company_table: Optional[Sequence[Sequence[float]]],
                segment_table: Optional[Sequence[Sequence[float]]], *,
                shared_counts: Optional[Mapping[str, int]] = None, bonus: float = SHARED_TERM_BONUS,
                limit: int = PEERS_MAX) -> list[dict]:
    """The peers list (§6.3, §6.4): ``[{"code", "tier", "company_match", "segment_match"}]``.

    ``company_rows`` (``stock_code``, ``similarity``) and ``segment_rows`` (one per company:
    ``stock_code``, ``seg_no``, ``similarity``) are merged by company. A side is graded with its
    own table; below the ``related`` tier it is no match (None). A company with no match on
    either side is left out. The row tier is the higher of the two; the rank goes by the higher
    of the two percentiles (plus ``bonus`` per shared term from ``shared_counts``), then the
    other percentile, the company similarity, the segment similarity and the code. At most
    ``limit`` rows.
    """
    sides: dict[str, dict] = {}
    for row in company_rows:
        sides.setdefault(row["stock_code"], {})["company"] = grade(row["similarity"], company_table)
    for row in segment_rows:
        graded = grade(row["similarity"], segment_table)
        sides.setdefault(row["stock_code"], {})["segment"] = (
            None if graded is None else {"seg_no": row["seg_no"], **graded})
    shared_counts = shared_counts or {}
    ranked = []
    for code, side in sides.items():
        company, segment = side.get("company"), side.get("segment")
        percentiles = [m["percentile"] for m in (company, segment) if m is not None]
        if not percentiles:
            continue
        best = max(percentiles)
        other = min(percentiles) if len(percentiles) == 2 else -1.0
        key = (-(best + bonus * shared_counts.get(code, 0)), -other,
               -(company["similarity"] if company else -2.0),
               -(segment["similarity"] if segment else -2.0), code)
        ranked.append((key, {"code": code, "tier": tier_of(best), "company_match": company,
                             "segment_match": segment}))
    ranked.sort(key=lambda item: item[0])
    return [peer for _, peer in ranked[:limit]]


def _shared_keys(seed_terms: Optional[Iterable[str]], peer_terms: Optional[Iterable[str]],
                 term_table: Mapping[str, Mapping[str, Any]]) -> list[str]:
    peer = set(peer_terms or [])
    keys = {key for key in seed_terms or [] if key in peer and key in term_table}
    return sorted(keys, key=lambda key: (term_table[key]["companies"], key))


def shared_terms(seed_terms: Optional[Iterable[str]], peer_terms: Optional[Iterable[str]],
                 term_table: Mapping[str, Mapping[str, Any]], limit: int = SHARED_TERMS_MAX) -> list[dict]:
    """``[{"term", "companies"}]``: the comparison keys both companies hold, rarest first (fewest
    companies in the build's term table, then by key), at most ``limit``, shown with the term
    table's display. Keys missing from the term table are left out; none shared → []."""
    return [{"term": term_table[key]["display"], "companies": term_table[key]["companies"]}
            for key in _shared_keys(seed_terms, peer_terms, term_table)[:limit]]


def shared_count(seed_terms: Optional[Iterable[str]], peer_terms: Optional[Iterable[str]],
                 term_table: Mapping[str, Mapping[str, Any]]) -> int:
    """How many keys of the term table both companies hold (for the shared-term bonus)."""
    return len(_shared_keys(seed_terms, peer_terms, term_table))


def same_industry(seed: Optional[str], peer: Optional[str]) -> Optional[bool]:
    """True when the stock list's ``산업명(중)`` is the same, False when it differs, None when
    either is blank."""
    seed, peer = (seed or "").strip(), (peer or "").strip()
    if not seed or not peer:
        return None
    return seed == peer


# ── the price reaction (§9) and the candidate flag (§10) ─────────────────────

WINDOWS: tuple[str, ...] = ("1w", "1m", "3m")      # 5, 21 and 63 trading days
DEFAULT_WINDOW = "1m"
JUDGE_MIN_EXCESS = 10.0     # the seed must beat its market by at least 10 %p (the user's value)
RHO_PARTIAL = 0.25
RHO_REACTED = 0.6
REACTIONS: tuple[str, ...] = ("none", "partial", "reacted", "undetermined")
MIN_AVG_VALUE_20D = 500_000_000   # 5억 원
NOT_CANDIDATE_REASONS: tuple[str, ...] = ("has_reports", "reacted", "undetermined", "not_traded",
                                          "low_liquidity", "holding", "weak_similarity")
# Flags that mean the stock is not trading or its numbers are not this run's.
_NO_TRADE_FLAGS = ("halted", "no_data")


def judgeable(seed_excess: Optional[float]) -> bool:
    """Whether reactions are judged: the seed's excess return is at least ``JUDGE_MIN_EXCESS``."""
    return seed_excess is not None and seed_excess >= JUDGE_MIN_EXCESS


def is_trading(price: Optional[Mapping[str, Any]]) -> bool:
    """A snapshot that says the stock trades (``traded`` true) with this run's numbers (no
    ``halted`` / ``no_data`` flag). No snapshot is not trading."""
    if price is None or price.get("traded") is not True:
        return False
    return not any(flag in _NO_TRADE_FLAGS for flag in price.get("flags") or [])


def reaction(seed_excess: Optional[float], seed_as_of: Optional[str],
             peer_price: Optional[Mapping[str, Any]], window: str) -> str:
    """The peer's reaction to the seed's rise over ``window`` (§9).

    ``undetermined`` when the seed is not judged (``judgeable``), the peer has no snapshot, is
    not trading (``is_trading``), has no excess return for the window, or its ``as_of`` differs
    from the seed's. Else ρ = peer excess ÷ seed excess: ``none`` below 0.25, ``partial`` from
    0.25, ``reacted`` from 0.6.
    """
    if not judgeable(seed_excess) or not is_trading(peer_price):
        return "undetermined"
    peer_excess = (peer_price.get("excess") or {}).get(window)
    if peer_excess is None or seed_as_of is None or peer_price.get("as_of") != seed_as_of:
        return "undetermined"
    rho = peer_excess / seed_excess
    if rho < RHO_PARTIAL:
        return "none"
    if rho < RHO_REACTED:
        return "partial"
    return "reacted"


def candidacy(*, label: Optional[str], reaction: str, price: Optional[Mapping[str, Any]],
              is_holding: Optional[bool], tier: Optional[str]) -> tuple[bool, list[str]]:
    """``(candidate, reasons)`` (§10). A candidate meets all six conditions: no stock report
    (label ``none``), reaction ``none`` or ``partial``, trading (``is_trading``), a 20-day average
    trading value of at least 5억 원, not a holding company (``is_holding`` False), and a row tier
    of ``related`` or above. Each unmet condition adds its code, in ``NOT_CANDIDATE_REASONS``
    order; an unknown value never meets its condition (``is_holding`` None gives ``holding``)."""
    reasons = []
    if label != "none":
        reasons.append("has_reports")
    if reaction == "reacted":
        reasons.append("reacted")
    elif reaction not in ("none", "partial"):
        reasons.append("undetermined")
    if not is_trading(price):
        reasons.append("not_traded")
    value = price.get("avg_value_20d") if price is not None else None
    if value is None or value < MIN_AVG_VALUE_20D:
        reasons.append("low_liquidity")
    if is_holding is not False:
        reasons.append("holding")
    if tier not in TIERS:
        reasons.append("weak_similarity")
    return not reasons, reasons


# ── theme search (§12.2) ─────────────────────────────────────────────────────

RRF_K = 60
QUERY_TERM_MIN_CHARS = 2
# Query pieces part at spaces, commas and middle dots.
_QUERY_PIECES = re.compile(rf"[\s,，、{re.escape(MIDDLE_DOTS)}]+")


def normalize_query(query: str, synonyms: Synonyms) -> str:
    """The query with runs of white space made one space and synonym spellings made standard: the
    whole query when it is one spelling, else each word (parted as in ``Synonyms.key``) that is
    one. This text is embedded."""
    text = " ".join((query or "").split())
    whole = synonyms.display(synonyms.key(text))
    if whole is not None:
        return whole
    parts = _WORDS.split(text)
    parts[::2] = [(synonyms.display(synonyms.key(word)) or word) if word else word for word in parts[::2]]
    return "".join(parts)


def query_terms(query: str, synonyms: Synonyms) -> list[tuple[str, str]]:
    """``[(comparison key, as written)]``: the whole query, then each piece of it (parted at
    spaces, commas and middle dots) of at least two characters; each key once, empty keys left
    out."""
    whole = (query or "").strip()
    pieces = [whole] + [piece for piece in _QUERY_PIECES.split(whole)
                        if len(piece.strip()) >= QUERY_TERM_MIN_CHARS]
    terms: dict[str, str] = {}
    for piece in pieces:
        key = synonyms.key(piece)
        if key and key not in terms:
            terms[key] = piece.strip()
    return list(terms.items())


def term_ranking(matched: Mapping[str, Sequence[str]], term_table: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Codes ranked by their matched query keys: more keys first, then fewer companies holding
    them (summed over the keys, from the term table; a key missing from it counts as one), then
    by code."""
    def companies(key: str) -> int:
        entry = term_table.get(key)
        return entry["companies"] if entry else 1

    return sorted(matched, key=lambda code: (-len(matched[code]), sum(companies(k) for k in matched[code]), code))


def rrf(rankings: Iterable[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: ``[(code, Σ 1 / (k + rank))]`` over the rankings (rank from 1),
    highest first, equal scores by code."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, code in enumerate(ranking, 1):
            scores[code] = scores.get(code, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
