"""Shared data model for the pipeline. Dataclasses + enums only — no logic.

Data flows: ``Config`` -> ``candidates`` -> ``Candidate`` -> ``signals`` ->
``SignalResult`` (+ optional ``PeMetadata``) -> ``scoring`` -> ``ScoredCandidate``
-> ``report``. Keeping this module dependency-free avoids import cycles and lets
every other module take plain data in and return plain data out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum


class ConfidenceTier(IntEnum):
    """Ordered confidence in an impersonation finding.

    ``IntEnum`` so tiers compare with ``>=`` (e.g. ``tier >= min_tier_to_report``).
    Only ``HIGH`` opens an issue by default; lower tiers are logged.
    """

    IGNORE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @classmethod
    def from_name(cls, name: str) -> ConfidenceTier:
        """Parse a tier from a case-insensitive name (for config input)."""
        try:
            return cls[name.strip().upper()]
        except KeyError as exc:
            valid = ", ".join(t.name for t in cls)
            raise ValueError(f"unknown tier {name!r}; expected one of: {valid}") from exc


@dataclass(frozen=True)
class Project:
    """The real project being defended."""

    owner: str
    name: str
    html_url: str
    created_at: datetime
    has_releases: bool
    source_dir_markers: tuple[str, ...] = ()
    description: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class ReleaseAsset:
    """A downloadable release artifact on a candidate repo."""

    name: str
    download_url: str
    content_type: str | None = None
    size: int = 0


@dataclass(frozen=True)
class Candidate:
    """A repo that *might* be impersonating the project.

    Fields after ``discovered_via`` are enriched lazily by the signals stage and
    default to ``None``/empty until then.
    """

    owner: str
    name: str
    html_url: str
    clone_url: str
    is_fork: bool
    created_at: datetime
    pushed_at: datetime
    discovered_via: str
    description: str | None = None
    has_releases: bool | None = None
    release_assets: tuple[ReleaseAsset, ...] = ()
    top_level_paths: tuple[str, ...] | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PeMetadata:
    """Version-resource fields extracted from a PE binary's bytes.

    Produced by ``pe.read_version_resources`` — byte parsing only, the binary is
    never executed. On any parse failure ``parse_ok`` is ``False`` and the run
    continues without a PE signal.
    """

    product_name: str | None = None
    company_name: str | None = None
    original_filename: str | None = None
    file_description: str | None = None
    raw_string_table: dict[str, str] = field(default_factory=dict)
    parse_ok: bool = False
    parse_error: str | None = None


@dataclass(frozen=True)
class SignalResult:
    """Outcome of one signal check against a candidate."""

    key: str
    label: str
    triggered: bool
    weight: float
    detail: str = ""
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredCandidate:
    """A candidate after scoring: signals, combined score, and tier."""

    candidate: Candidate
    signals: tuple[SignalResult, ...]
    score: float
    tier: ConfidenceTier
    pe_metadata: PeMetadata | None = None

    @property
    def triggered_signals(self) -> tuple[SignalResult, ...]:
        return tuple(s for s in self.signals if s.triggered)
