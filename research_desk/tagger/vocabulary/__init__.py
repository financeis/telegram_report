"""Publisher dictionary lookups (``publishers.yaml`` in this folder).

``publishers.yaml`` is the only source of publisher names. Each section
(``broker``/``data_provider``/``ir_agency``/``other`` — the publisher types of
``domain/vocabulary.yaml``) lists entries with a canonical name and aliases. A
stored publisher is a canonical name or null, and its type is the section it is
in. The aliases include the tag the Telegram channel puts at the end of a file
name (``..._20260511_MERITZ_1096333.pdf`` → ``MERITZ``).

The filename tag never decides the stored publisher. It only marks a suspect
publisher and picks rows to classify again.

Pure lookups: no DB, no network, no settings. The bundled file is read on first
use and kept; ``load_dictionary(path)`` reads another file. A broken file raises
``PublisherDictionaryError``. ``prompts.py`` reads the same file by path and puts
its text, comments included, into the system prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

import yaml

from research_desk.domain.reports import PUBLISHER_TYPES

PUBLISHERS_PATH = Path(__file__).with_name("publishers.yaml")

# `_YYYYMMDD_<tag>_<digits>.pdf` at the very end of the file name.
_FILENAME_TAG = re.compile(r"_[0-9]{8}_([^_]+)_[0-9]+\.pdf\Z")
# Tag characters besides letters (any script), as seen in the channel's tags:
# `Mirae+Asset`, `Hyundai Motor`; `&`, `.`, `-` are allowed for names like `A&B`.
_TAG_PUNCTUATION = frozenset("+&.- ")

_ENTRY_KEYS = frozenset({"canonical", "aliases"})


class PublisherDictionaryError(ValueError):
    """The publisher dictionary file cannot be read or breaks a dictionary rule."""


@dataclass(frozen=True)
class PublisherDictionary:
    """A loaded publisher dictionary.

    ``canonical_names``: in file order. ``types``: canonical name → section.
    ``names``: canonical name or alias → canonical name.
    """

    canonical_names: tuple[str, ...]
    types: Mapping[str, str]
    names: Mapping[str, str]

    def publisher_type(self, canonical: Optional[str]) -> Optional[str]:
        """The section of a canonical name; None for anything else (aliases included)."""
        return self.types.get(canonical) if canonical is not None else None

    def canonical_for(self, name: Optional[str]) -> Optional[str]:
        """The canonical name for an exact canonical name, alias or filename tag; else None."""
        return self.names.get(name) if name is not None else None

    def filename_publisher(self, file_name: Optional[str]) -> Optional[str]:
        """The canonical name the file name's tag points to; None without a known tag."""
        return self.canonical_for(filename_tag(file_name))


def filename_tag(file_name: Optional[str]) -> Optional[str]:
    """The channel's tag when ``file_name`` ends with ``_YYYYMMDD_<tag>_<digits>.pdf``.

    The tag has at least one letter and only letters, spaces and ``+ & . -``
    (so ``+`` or ``31780`` is not a tag). Anything else gives None.
    """
    if not file_name:
        return None
    match = _FILENAME_TAG.search(file_name)
    if match is None:
        return None
    tag = match.group(1)
    if not all(ch.isalpha() or ch in _TAG_PUNCTUATION for ch in tag):
        return None
    if not any(ch.isalpha() for ch in tag):
        return None
    return tag


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PublisherDictionaryError(f"{what} must be a non-empty string without outer spaces: {value!r}")
    return value


def load_dictionary(path: Path = PUBLISHERS_PATH) -> PublisherDictionary:
    """Read and check a publisher dictionary file (default: the bundled one).

    Rules: sections are publisher types; entries have ``canonical`` and optional
    ``aliases`` (a list); canonical names are unique; an alias belongs to one entry
    only, once, and is no entry's canonical name.
    """
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PublisherDictionaryError(f"cannot read the publisher dictionary: {exc}") from exc
    if not isinstance(data, dict):
        raise PublisherDictionaryError("the top level must map each section to a list of entries")

    entries: list[tuple[str, str, list[str]]] = []
    for section, items in data.items():
        if section not in PUBLISHER_TYPES:
            raise PublisherDictionaryError(f"unknown section {section!r}; sections are {PUBLISHER_TYPES}")
        if not isinstance(items, list):
            raise PublisherDictionaryError(f"section {section!r} must be a list of entries")
        for item in items:
            if not isinstance(item, dict) or "canonical" not in item:
                raise PublisherDictionaryError(f"an entry in {section!r} has no canonical name: {item!r}")
            unknown = set(item) - _ENTRY_KEYS
            if unknown:
                raise PublisherDictionaryError(f"unknown keys {sorted(unknown)} in entry {item!r}")
            canonical = _text(item["canonical"], f"canonical name in {section!r}")
            aliases = item.get("aliases") or []
            if not isinstance(aliases, list):
                raise PublisherDictionaryError(f"aliases of {canonical!r} must be a list")
            entries.append((section, canonical, [_text(a, f"alias of {canonical!r}") for a in aliases]))

    types: dict[str, str] = {}
    for section, canonical, _ in entries:
        if canonical in types:
            raise PublisherDictionaryError(f"canonical name {canonical!r} appears twice")
        types[canonical] = section

    names: dict[str, str] = {canonical: canonical for canonical in types}
    for _, canonical, aliases in entries:
        for alias in aliases:
            if alias in types:
                raise PublisherDictionaryError(
                    f"alias {alias!r} of {canonical!r} is a canonical name")
            if alias in names:
                raise PublisherDictionaryError(
                    f"alias {alias!r} belongs to both {names[alias]!r} and {canonical!r}")
            names[alias] = canonical

    return PublisherDictionary(
        canonical_names=tuple(canonical for _, canonical, _ in entries),
        types=MappingProxyType(types),
        names=MappingProxyType(names),
    )


@lru_cache(maxsize=1)
def default_dictionary() -> PublisherDictionary:
    """The bundled dictionary, read on first use and kept (a failed read is retried)."""
    return load_dictionary(PUBLISHERS_PATH)


def canonical_names() -> tuple[str, ...]:
    """Canonical publisher names of the bundled dictionary, in file order."""
    return default_dictionary().canonical_names


def publisher_type(canonical: Optional[str]) -> Optional[str]:
    """The publisher type of a canonical name; None for an alias, unknown name or None."""
    return default_dictionary().publisher_type(canonical)


def canonical_for(name: Optional[str]) -> Optional[str]:
    """The canonical name for an exact canonical name, alias or filename tag; else None."""
    return default_dictionary().canonical_for(name)


def filename_publisher(file_name: Optional[str]) -> Optional[str]:
    """The canonical name the file name's tag points to; None without a known tag."""
    return default_dictionary().filename_publisher(file_name)
