"""The publisher dictionary (vocabulary/publishers.yaml) and its lookups (spec §3.3, §3.4).

- Dictionary rules, read straight from the yaml so a lookup table cannot hide a duplicate:
  the four sections are the publisher types, canonical names are unique, every alias
  (filename tags included) belongs to exactly one entry and is no other entry's canonical
  name, every filename tag observed in the collected data is covered.
- The cleanup decisions: 미래대우증권 is gone, renamed and moved entries, removed aliases.
- The lookups in ``research_desk.tagger.vocabulary``: canonical names, type by canonical
  name, alias → canonical name, the filename tag and the publisher it points to.
- ``load_dictionary`` refuses a broken file with ``PublisherDictionaryError``.
"""
from __future__ import annotations

import textwrap

import pytest
import yaml

from research_desk.domain.reports import PUBLISHER_TYPES
from research_desk.tagger import vocabulary
from research_desk.tagger.prompts import SYSTEM_PROMPT
from research_desk.tagger.vocabulary import (
    PUBLISHERS_PATH,
    PublisherDictionaryError,
    canonical_for,
    canonical_names,
    default_dictionary,
    filename_publisher,
    filename_tag,
    load_dictionary,
    publisher_type,
)

# Every filename tag (`_YYYYMMDD_<tag>_<digits>.pdf`) in reports.file_name of the
# collected data, read from the production DB on 2026-10-10. A new tag in the channel
# is not a test failure; add it here when it is added to publishers.yaml.
OBSERVED_FILENAME_TAGS = (
    "ARC", "Aris", "BNK", "Bookook", "Brius+Insight", "Bulit", "CMIR", "CTT+Reserch",
    "DAOL", "DB", "DS", "Daishin", "Eugene", "Finlit", "Foundation+Investment+Advisors",
    "GL+Research", "Growth+Research", "Hana", "Hanwha", "Hanyang", "Hungkuk",
    "Hyundai Motor", "Hyundai+Motor", "IBK", "IRKUDOS", "IV+Research", "Irbiznet", "KB",
    "KCMI", "KDB+Future+Strategy+Institute", "KIRS", "Kiwoom", "Konnect", "Korea", "Kyobo",
    "LS", "Leading", "Lightwood+IRI", "Lightwood+Partners", "MERITZ",
    "Mint+Investment+Advisors", "Mirae Asset", "Mirae+Asset", "NH", "SK", "Samsung",
    "Samsung+Futures", "Sangsangin", "Shinhan", "Shinyoung", "Small+Insight",
    "Stunning+Value+Research", "Tiger+Research", "Yuanta", "Yuhwa", "buffettlab",
    "dyneasset", "iM", "해당기업",
)


def _raw() -> dict:
    return yaml.safe_load(PUBLISHERS_PATH.read_text(encoding="utf-8"))


def _entries() -> list[tuple[str, str, list[str]]]:
    """(section, canonical, aliases) in file order."""
    return [(section, entry["canonical"], list(entry.get("aliases") or []))
            for section, entries in _raw().items() for entry in entries]


# ── the dictionary file ──────────────────────────────────────────────────────


def test_sections_are_the_publisher_types():
    assert tuple(_raw()) == PUBLISHER_TYPES


def test_entries_have_only_a_canonical_name_and_aliases():
    for entries in _raw().values():
        for entry in entries:
            assert set(entry) <= {"canonical", "aliases"}, entry
            assert isinstance(entry["canonical"], str) and entry["canonical"].strip() == entry["canonical"] != ""
            for alias in entry.get("aliases") or []:
                assert isinstance(alias, str) and alias.strip() == alias != "", (entry["canonical"], alias)


def test_canonical_names_are_unique():
    names = [canonical for _, canonical, _ in _entries()]
    assert len(names) == len(set(names))


def test_every_alias_belongs_to_exactly_one_entry():
    seen: dict[str, str] = {}
    for _, canonical, aliases in _entries():
        for alias in aliases:
            assert alias not in seen, f"{alias!r} is an alias of both {seen[alias]!r} and {canonical!r}"
            seen[alias] = canonical


def test_no_alias_equals_a_canonical_name():
    canonicals = {canonical for _, canonical, _ in _entries()}
    for _, canonical, aliases in _entries():
        assert not canonicals & set(aliases), (canonical, sorted(canonicals & set(aliases)))


def test_every_observed_filename_tag_is_covered():
    missing = [tag for tag in OBSERVED_FILENAME_TAGS if canonical_for(tag) is None]
    assert missing == []


def test_the_observed_tags_are_the_ones_the_extractor_finds():
    for tag in OBSERVED_FILENAME_TAGS:
        assert filename_tag(f"회사［000000］_20260101_{tag}_1234567.pdf") == tag


def test_miraedaewoo_is_gone():
    names = {canonical for _, canonical, _ in _entries()}
    aliases = {alias for _, _, entry_aliases in _entries() for alias in entry_aliases}
    assert "미래대우증권" not in names | aliases
    assert "미래대우" not in names | aliases


@pytest.mark.parametrize("writer", [
    "한국기업평가", "NICE신용평가", "나이스신용평가", "NICE평가정보", "나이스평가정보",
    "한국기술신용평가", "서울평가정보",
])
def test_tech_analysis_writers_point_to_kirs_not_to_their_own_entry(writer):
    # 기술분석보고서의 발행처는 작성기관과 상관없이 한국IR협의회다 (2026-10-10 사용자 결정).
    assert writer not in canonical_names()
    assert canonical_for(writer) == "한국IR협의회"


@pytest.mark.parametrize("alias", ["현대증권", "NH우리", "KIS", "KIS rating", "KR", "하이"])
def test_wrong_or_ambiguous_aliases_are_removed(alias):
    assert canonical_for(alias) is None


@pytest.mark.parametrize("old, new", [
    ("DB금융투자", "DB증권"),
    ("KIRS", "한국IR협의회"),
    ("하이투자증권", "iM증권"),
    ("하이투자", "iM증권"),
    ("FnGuide", "에프앤가이드"),
    ("KRX", "한국거래소"),
    ("GL Research", "지엘리서치"),
    ("dyneasset", "다인자산운용"),
    ("IRKUDOS", "IR큐더스"),
])
def test_old_names_are_aliases_of_the_current_name(old, new):
    assert old not in canonical_names()
    assert canonical_for(old) == new


@pytest.mark.parametrize("canonical, kind", [
    ("신영증권", "broker"),
    ("한양증권", "broker"),
    ("리딩투자증권", "broker"),
    ("지엘리서치", "data_provider"),
    ("밸류파인더", "data_provider"),
    ("한국IR협의회", "data_provider"),
    ("IR큐더스", "ir_agency"),
    ("해당기업", "other"),
])
def test_named_entries_and_their_types(canonical, kind):
    assert publisher_type(canonical) == kind


@pytest.mark.parametrize("tag, canonical", [
    ("DAOL", "다올투자증권"),       # the old model wrote 대신증권
    ("Daishin", "대신증권"),
    ("Shinyoung", "신영증권"),      # not 신한투자증권
    ("Shinhan", "신한투자증권"),
    ("KIRS", "한국IR협의회"),
    ("Hungkuk", "흥국증권"),
    ("MERITZ", "메리츠증권"),
    ("Korea", "한국투자증권"),
    ("Samsung", "삼성증권"),
    ("Samsung+Futures", "삼성선물"),
    ("Hyundai+Motor", "현대차증권"),
    ("Hyundai Motor", "현대차증권"),
    ("Mirae+Asset", "미래에셋증권"),
    ("Leading", "리딩투자증권"),
    ("Bookook", "부국증권"),
    ("해당기업", "해당기업"),
])
def test_known_tags_point_to_the_right_publisher(tag, canonical):
    assert canonical_for(tag) == canonical


def test_the_header_comment_is_current():
    text = PUBLISHERS_PATH.read_text(encoding="utf-8")
    assert "lookup_publisher" not in text


def test_the_system_prompt_embeds_the_same_file():
    assert PUBLISHERS_PATH.read_text(encoding="utf-8") in SYSTEM_PROMPT


# ── lookups ──────────────────────────────────────────────────────────────────


def test_canonical_names_follow_the_file_order():
    names = canonical_names()
    assert isinstance(names, tuple)
    assert names == tuple(canonical for _, canonical, _ in _entries())


def test_the_type_of_each_canonical_name_is_its_section():
    for section, canonical, _ in _entries():
        assert publisher_type(canonical) == section


@pytest.mark.parametrize("name", [None, "", "모르는증권", "Meritz", "키움"])
def test_the_type_is_only_given_for_a_canonical_name(name):
    # An alias ("Meritz", "키움") is not a stored value, so it has no type.
    assert publisher_type(name) is None


def test_canonical_for_maps_canonical_names_and_aliases():
    for _, canonical, aliases in _entries():
        assert canonical_for(canonical) == canonical
        for alias in aliases:
            assert canonical_for(alias) == canonical


@pytest.mark.parametrize("name", [None, "", "모르는증권", "meritz", "MERITZ ", " MERITZ", "메리츠증권 리서치센터"])
def test_canonical_for_is_exact(name):
    assert canonical_for(name) is None


@pytest.mark.parametrize("file_name, tag", [
    ("엠씨넥스［097520］_20260511_MERITZ_1096333.pdf", "MERITZ"),
    ("산업_반도체_Semiconductors_20250507_Hana_1001234.pdf", "Hana"),
    ("GS건설［006360］_20260710_Mirae+Asset_1112141.pdf", "Mirae+Asset"),
    ("현대제철［004020］_20260710_Hyundai Motor_1112118.pdf", "Hyundai Motor"),
    ("올릭스［226950］회사소개_20250701_해당기업_1011264.pdf", "해당기업"),
    ("x_20260101_A&B.C-D_1.pdf", "A&B.C-D"),
    ("_20260101_iM_0.pdf", "iM"),
])
def test_filename_tag(file_name, tag):
    assert filename_tag(file_name) == tag


@pytest.mark.parametrize("file_name", [
    None,
    "",
    "엠씨넥스［097520］_MERITZ_1096333.pdf",                   # no date
    "엠씨넥스［097520］_2026051_MERITZ_1096333.pdf",           # 7-digit date
    "엠씨넥스［097520］_202605110_MERITZ_1096333.pdf",         # 9-digit date
    "엠씨넥스［097520］20260511_MERITZ_1096333.pdf",           # no underscore before the date
    "삼성전자［005930］전망_20250502_KB_997304_1.pdf",          # something after the number
    "엔비티［236810］성장기_20250623_Korea+Investor+Relations+S.pdf",  # no number
    "엠씨넥스［097520］_20260511_MERITZ_abc.pdf",              # the number is not digits
    "엠씨넥스［097520］_20260511_MERITZ_.pdf",                 # empty number
    "엠씨넥스［097520］_20260511__1096333.pdf",                # empty tag
    "엠씨넥스［097520］_20260511_MERITZ_1096333.PDF",          # not .pdf
    "엠씨넥스［097520］_20260511_MERITZ_1096333.pdf.pdf",      # not at the end
    "엠씨넥스［097520］_20260511_MERITZ_1096333",              # no extension
    "엠씨넥스［097520］_20260511_MERITZ_1096333.pdf\n",        # trailing newline
    "한라IMS_20260414_092460.pdf.pdf",
    "파이버프로_368770;방산+데이터센터=_밝은미래!_260730.pdf",
])
def test_filename_tag_needs_the_exact_shape(file_name):
    assert filename_tag(file_name) is None


@pytest.mark.parametrize("tag", ["+", "31780", "+&.-", " ", "++", "K2", "삼성/증권", "Hana!"])
def test_degenerate_tags_are_not_tags(tag):
    # A tag needs a letter, and only letters, spaces and + & . - are tag characters.
    assert filename_tag(f"원일티엔아이［136150］기업_20250704_{tag}_1011950.pdf") is None


@pytest.mark.parametrize("file_name, publisher", [
    ("엠씨넥스［097520］_20260511_MERITZ_1096333.pdf", "메리츠증권"),
    ("x_20260929_Leading_1134181.pdf", "리딩투자증권"),
    ("x_20260929_Samsung+Futures_1.pdf", "삼성선물"),
    ("x_20260929_Samsung_1.pdf", "삼성증권"),
    ("x_20250701_해당기업_1011264.pdf", "해당기업"),
])
def test_filename_publisher(file_name, publisher):
    assert filename_publisher(file_name) == publisher


@pytest.mark.parametrize("file_name", [
    None,
    "x_20260511_Nowhere+Research_1096333.pdf",   # a tag the dictionary does not know
    "x_20260511_meritz_1096333.pdf",             # tags match exactly
    "x_20250704_+_1011950.pdf",                  # not a tag
    "삼성전자［005930］전망_20250502_KB_997304_1.pdf",  # no tag
    "산업_화학_Weekly_Monitor_20250512.pdf",
])
def test_filename_points_to_no_publisher(file_name):
    assert filename_publisher(file_name) is None


def test_the_default_dictionary_is_read_once():
    assert default_dictionary() is default_dictionary()
    assert canonical_names() is default_dictionary().canonical_names


# ── load_dictionary on other files ───────────────────────────────────────────


def _write(tmp_path, text: str):
    path = tmp_path / "publishers.yaml"
    path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return path


def test_load_dictionary_reads_a_given_file(tmp_path):
    path = _write(tmp_path, """
        broker:
          - canonical: 가증권
            aliases: ["Ga", "가"]
        other:
          - canonical: 해당기업
            aliases: []
          - canonical: 나협회
    """)
    loaded = load_dictionary(path)
    assert loaded.canonical_names == ("가증권", "해당기업", "나협회")
    assert loaded.publisher_type("가증권") == "broker"
    assert loaded.publisher_type("나협회") == "other"
    assert loaded.canonical_for("Ga") == "가증권"
    assert loaded.filename_publisher("x_20260101_Ga_1.pdf") == "가증권"
    assert loaded.filename_publisher("x_20260101_Na_1.pdf") is None


@pytest.mark.parametrize("text, words", [
    ("""
        broker:
          - canonical: 가증권
          - canonical: 가증권
     """, "가증권"),
    ("""
        broker:
          - canonical: 가증권
            aliases: ["Ga"]
        data_provider:
          - canonical: 나리서치
            aliases: ["Ga"]
     """, "Ga"),
    ("""
        broker:
          - canonical: 가증권
            aliases: ["Ga", "Ga"]
     """, "Ga"),
    ("""
        broker:
          - canonical: 가증권
            aliases: ["나리서치"]
        data_provider:
          - canonical: 나리서치
     """, "나리서치"),
    ("""
        broker:
          - canonical: 가증권
            aliases: ["가증권"]
     """, "가증권"),
    ("""
        company:
          - canonical: 해당기업
     """, "company"),
    ("""
        broker:
          - aliases: ["Ga"]
     """, "canonical"),
    ("""
        broker:
          - canonical: ""
     """, "canonical"),
    ("""
        broker:
          - canonical: 가증권
            aliases: "Ga"
     """, "aliases"),
    ("""
        broker:
          - canonical: 가증권
            type: broker
     """, "type"),
    ("""
        broker: 가증권
     """, "broker"),
    ("- 가증권\n", "section"),
], ids=["duplicate canonical", "alias in two entries", "alias twice in one entry",
        "alias is another canonical", "alias is its own canonical", "unknown section",
        "no canonical", "empty canonical", "aliases not a list", "unknown key",
        "section not a list", "not a mapping"])
def test_load_dictionary_refuses_a_broken_file(tmp_path, text, words):
    with pytest.raises(PublisherDictionaryError, match=words):
        load_dictionary(_write(tmp_path, text))


def test_load_dictionary_refuses_a_missing_or_unparsable_file(tmp_path):
    with pytest.raises(PublisherDictionaryError):
        load_dictionary(tmp_path / "missing.yaml")
    with pytest.raises(PublisherDictionaryError):
        load_dictionary(_write(tmp_path, "broker: [\n"))


def test_the_module_reads_no_settings(monkeypatch):
    # Pure lookups: a fresh load does not touch the environment or the DB.
    monkeypatch.delenv("STORAGE_BASE_DIR", raising=False)
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    assert load_dictionary().canonical_names == canonical_names()
    assert vocabulary.PUBLISHERS_PATH.name == "publishers.yaml"
