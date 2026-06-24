"""Evaluate structural signals for a candidate against the real project.

Each ``signal_*`` function is pure given its inputs and returns a SignalResult
whose weight comes from the canonical table in ``scoring`` (single source of
truth). ``evaluate`` enriches the candidate via the injected GitHub client
(tree, releases, and a byte-only PE read of any direct PE asset) and runs every
signal. Weak/decaying behavioral signals (README churn) are out of MVP scope.
"""

from __future__ import annotations

import re
from dataclasses import replace

from .config import Config
from .github_io import GitHubClient, GitHubError
from .models import Candidate, PeMetadata, Project, ReleaseAsset, SignalResult
from .pe import is_pe_asset, read_version_resources
from .scoring import SIGNAL_WEIGHTS

# Hard cap on a PE asset we will pull into memory to parse. Direct PE installers
# are small; anything larger is skipped rather than downloaded.
MAX_PE_BYTES = 64 * 1024 * 1024

_OTHER_OS_TOKENS = ("macos", "mac os", "osx", "linux", "android", "ios")
_WINDOWS_TOKENS = ("windows", "win32", "win64", "win ")
_WINDOWS_ASSET_EXT = (".exe", ".msi")


def _result(
    key: str, *, triggered: bool, detail: str = "", evidence: dict | None = None
) -> SignalResult:
    return SignalResult(
        key=key,
        label=key.replace("_", " "),
        triggered=triggered,
        weight=SIGNAL_WEIGHTS[key],
        detail=detail,
        evidence=evidence or {},
    )


def _normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _names_relate(name: str | None, project_name: str) -> bool:
    a, b = _normalize(name), _normalize(project_name)
    if not a or not b:
        return False
    return a in b or b in a


def _descriptions_match(candidate: Candidate, project: Project) -> bool:
    a = (candidate.description or "").strip().lower()
    b = (project.description or "").strip().lower()
    if not a or not b:
        return False
    return a == b or a in b or b in a


def _assets_from_releases(releases: list[dict]) -> tuple[ReleaseAsset, ...]:
    assets: list[ReleaseAsset] = []
    for rel in releases:
        for asset in rel.get("assets", []):
            assets.append(
                ReleaseAsset(
                    name=asset.get("name", ""),
                    download_url=asset.get("browser_download_url", ""),
                    content_type=asset.get("content_type"),
                    size=asset.get("size", 0),
                )
            )
    return tuple(assets)


# --- individual signals ---------------------------------------------------

def signal_owner_mismatch(candidate: Candidate, project: Project) -> SignalResult:
    triggered = candidate.owner.lower() != project.owner.lower()
    return _result(
        "owner_mismatch",
        triggered=triggered,
        detail=(
            f"Owner '{candidate.owner}' is not the legitimate maintainer "
            f"'{project.owner}'."
        ),
    )


def signal_not_a_fork(candidate: Candidate) -> SignalResult:
    return _result(
        "not_a_fork",
        triggered=not candidate.is_fork,
        detail="Repo is not a GitHub fork (attackers avoid forking to dodge attribution).",
    )


def signal_created_after_real(candidate: Candidate, project: Project) -> SignalResult:
    triggered = candidate.created_at > project.created_at
    return _result(
        "created_after_real",
        triggered=triggered,
        detail=(
            f"Created {candidate.created_at:%Y-%m-%d}, after the real project "
            f"({project.created_at:%Y-%m-%d})."
        ),
    )


def signal_source_tree_stripped(candidate: Candidate, project: Project) -> SignalResult:
    """README/description match the project, but the source tree is stripped.

    Conservative: requires the description to look copied AND known source
    markers to be absent from the candidate's top-level tree. If the tree is
    unknown or the description differs, it does not trigger.
    """
    paths = candidate.top_level_paths
    if not paths or not project.source_dir_markers or not _descriptions_match(candidate, project):
        return _result("source_tree_stripped", triggered=False)
    lowered = {p.lower() for p in paths}
    markers = {m.lower() for m in project.source_dir_markers}
    present = markers & lowered
    triggered = not present
    return _result(
        "source_tree_stripped",
        triggered=triggered,
        detail=(
            "README/description match the real project, but the top-level tree "
            f"contains none of its source directories ({', '.join(sorted(markers))})."
        ),
        evidence={"top_level": sorted(lowered), "expected_markers": sorted(markers)},
    )


def signal_ships_binary_real_does_not(candidate: Candidate, project: Project) -> SignalResult:
    candidate_ships = bool(candidate.has_releases and candidate.release_assets)
    triggered = candidate_ships and not project.has_releases
    names = [a.name for a in candidate.release_assets]
    return _result(
        "ships_binary_real_does_not",
        triggered=triggered,
        detail=(
            f"Ships a binary release ({', '.join(names) or 'asset'}); the real "
            "project publishes no binaries."
        ),
        evidence={"assets": names},
    )


def signal_platform_path_inconsistency(candidate: Candidate, project: Project) -> SignalResult:
    """A Windows-only artifact under a repo that advertises another platform."""
    desc = (candidate.description or "").lower()
    has_windows_asset = any(
        a.name.lower().endswith(_WINDOWS_ASSET_EXT) for a in candidate.release_assets
    )
    mentions_other_os = any(tok in desc for tok in _OTHER_OS_TOKENS)
    mentions_windows = any(tok in desc for tok in _WINDOWS_TOKENS)
    triggered = has_windows_asset and mentions_other_os and not mentions_windows
    return _result(
        "platform_path_inconsistency",
        triggered=triggered,
        detail=(
            "Describes a non-Windows platform but the only release artifact is a "
            "Windows executable."
        ),
    )


def signal_pe_metadata_mismatch(
    candidate: Candidate, project: Project, pe_metadata: PeMetadata | None
) -> SignalResult:
    """The binary's embedded version resources name an unrelated product."""
    if pe_metadata is None or not pe_metadata.parse_ok:
        return _result("pe_metadata_mismatch", triggered=False)
    embedded = [n for n in (pe_metadata.product_name, pe_metadata.company_name) if n]
    if not embedded:
        return _result("pe_metadata_mismatch", triggered=False)
    relates = any(_names_relate(n, project.name) for n in embedded)
    triggered = not relates
    return _result(
        "pe_metadata_mismatch",
        triggered=triggered,
        detail=(
            "The release binary's embedded version resources name an unrelated "
            f"product: ProductName={pe_metadata.product_name!r}, "
            f"CompanyName={pe_metadata.company_name!r}."
        ),
        evidence={
            "product_name": pe_metadata.product_name,
            "company_name": pe_metadata.company_name,
        },
    )


# --- enrichment + orchestration ------------------------------------------

def _enrich_tree(candidate: Candidate, gh: GitHubClient) -> Candidate:
    try:
        paths = gh.get_tree_top_level(candidate.full_name)
    except GitHubError:
        return candidate  # leaves top_level_paths as None
    return replace(candidate, top_level_paths=tuple(paths))


def _enrich_releases(candidate: Candidate, gh: GitHubClient) -> Candidate:
    try:
        releases = gh.list_releases(candidate.full_name)
    except GitHubError:
        return replace(candidate, has_releases=False, release_assets=())
    return replace(
        candidate,
        has_releases=bool(releases),
        release_assets=_assets_from_releases(releases),
    )


def _read_pe(candidate: Candidate, gh: GitHubClient) -> PeMetadata | None:
    pe_asset = next((a for a in candidate.release_assets if is_pe_asset(a.name)), None)
    if pe_asset is None:
        return None
    try:
        data = gh.download_asset(pe_asset.download_url, max_bytes=MAX_PE_BYTES)
    except GitHubError:
        return None
    return read_version_resources(data)


def evaluate(
    candidate: Candidate, project: Project, config: Config, gh: GitHubClient
) -> tuple[Candidate, list[SignalResult], PeMetadata | None]:
    """Enrich the candidate and evaluate all structural signals."""
    candidate = _enrich_tree(candidate, gh)
    candidate = _enrich_releases(candidate, gh)
    pe_metadata = _read_pe(candidate, gh)

    results = [
        signal_owner_mismatch(candidate, project),
        signal_not_a_fork(candidate),
        signal_created_after_real(candidate, project),
        signal_source_tree_stripped(candidate, project),
        signal_ships_binary_real_does_not(candidate, project),
        signal_platform_path_inconsistency(candidate, project),
        signal_pe_metadata_mismatch(candidate, project, pe_metadata),
    ]
    return candidate, results, pe_metadata
