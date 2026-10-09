"""Peers settings: the profile models, versions, batch limits, the business-report MongoDB, and
the synonym table file.

Read when called, never at import (commands call ``core.settings.load_env()`` first). Model
names follow ``LLM_MODEL_<ROLE>`` → legacy ``OPENAI_MODEL_<ROLE>`` → default; the model name
picks the provider (``core.llm``). Empty text values count as unset. A malformed number raises
ValueError naming the variable (the command then ends with exit code 1, like other number
settings). API keys and DB settings are shared values read with the ``core.settings`` getters.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from research_desk.core import settings as core_settings

if TYPE_CHECKING:
    from .logic import Synonyms

DEFAULT_PROFILE_MODEL = "claude-haiku-5-5"        # LLM_MODEL_PEERS (provisional: chosen after pilots)
DEFAULT_ESCALATION_MODEL = "gpt-5.4"               # LLM_MODEL_PEERS_ESCALATION
DEFAULT_PROFILE_VERSION = "peer-profile@1.0"       # PEERS_PROFILE_VERSION: profiles are keyed by it
DEFAULT_EMBED_MODEL = "text-embedding-3-large"     # PEERS_EMBED_MODEL, sent with dimensions=1536
DEFAULT_FISCAL_YEAR = 2025                         # PEERS_FISCAL_YEAR
# Operating ceiling, like tagging: the providers' tokens-per-minute limit is the bottleneck.
# Raise only after checking the limit and the token trend.
DEFAULT_MAX_CONCURRENT_LLM = 2                     # PEERS_MAX_CONCURRENT_LLM
DEFAULT_PER_COMPANY_TIMEOUT_S = 120                # PEERS_PER_COMPANY_TIMEOUT_S
DEFAULT_MONGO_URL = "mongodb://localhost:27017/"   # DART_MONGO_URL
DEFAULT_MONGO_DB = "FS"                            # DART_MONGO_DB
DEFAULT_MONGO_COLLECTION = "A001_v2"               # DART_MONGO_COLLECTION

SYNONYMS_PATH = Path(__file__).with_name("synonyms.yaml")


@dataclass(frozen=True)
class PeersSettings:
    profile_model: str
    escalation_model: str
    profile_version: str
    embed_model: str
    fiscal_year: int
    max_concurrent_llm: int
    per_company_timeout_s: float
    mongo_url: str
    mongo_db: str
    mongo_collection: str


def _text(name: str, default: str) -> str:
    return core_settings.optional(name) or default


def profile_model() -> str:
    """The model that extracts profiles."""
    return core_settings.model_name("PEERS", DEFAULT_PROFILE_MODEL)


def escalation_model() -> str:
    """The model that extracts once more when grounding is below 0.8."""
    return core_settings.model_name("PEERS_ESCALATION", DEFAULT_ESCALATION_MODEL)


def profile_version() -> str:
    return _text("PEERS_PROFILE_VERSION", DEFAULT_PROFILE_VERSION)


def embed_model() -> str:
    return _text("PEERS_EMBED_MODEL", DEFAULT_EMBED_MODEL)


def fiscal_year() -> int:
    return core_settings.get_int("PEERS_FISCAL_YEAR", DEFAULT_FISCAL_YEAR)


def max_concurrent_llm() -> int:
    """AI calls in flight at once during a build (at least 1)."""
    value = core_settings.get_int("PEERS_MAX_CONCURRENT_LLM", DEFAULT_MAX_CONCURRENT_LLM)
    if value < 1:
        raise ValueError(f"PEERS_MAX_CONCURRENT_LLM must be at least 1, got {value}")
    return value


def per_company_timeout_s() -> int:
    """Time limit for one company's AI extraction (escalation included)."""
    return core_settings.get_int("PEERS_PER_COMPANY_TIMEOUT_S", DEFAULT_PER_COMPANY_TIMEOUT_S)


def load_settings() -> PeersSettings:
    """Current values from the process environment."""
    return PeersSettings(
        profile_model=profile_model(),
        escalation_model=escalation_model(),
        profile_version=profile_version(),
        embed_model=embed_model(),
        fiscal_year=fiscal_year(),
        max_concurrent_llm=max_concurrent_llm(),
        per_company_timeout_s=per_company_timeout_s(),
        mongo_url=_text("DART_MONGO_URL", DEFAULT_MONGO_URL),
        mongo_db=_text("DART_MONGO_DB", DEFAULT_MONGO_DB),
        mongo_collection=_text("DART_MONGO_COLLECTION", DEFAULT_MONGO_COLLECTION),
    )


def synonyms(path: Optional[Path] = None) -> "Synonyms":
    """The synonym table (``synonyms.yaml`` next to this file unless ``path`` is given).

    Raises ValueError for a malformed or ambiguous table and OSError when it cannot be read.
    YAML and the calculations load here, not when this module is imported.
    """
    import yaml

    from .logic import parse_synonyms

    data = yaml.safe_load(Path(path or SYNONYMS_PATH).read_text(encoding="utf-8"))
    return parse_synonyms(data or {})
