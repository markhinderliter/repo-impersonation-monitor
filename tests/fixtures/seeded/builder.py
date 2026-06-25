"""Build synthetic repo snapshots with known correct answers, in temp dirs.

Pure local: no network, and synthesized PE bytes are only ever parsed by the
scorer (via pe.read_version_resources), never executed. ``LocalSnapshotClient``
stands in for the GitHub client, serving tree/releases/asset-bytes from disk so
``signals.evaluate`` runs the real detection path end-to-end offline.

The three cases are deliberately constructed so the near-miss (benign_mirror)
shares as much hostile *surface* with the evil twin as possible — same
different-owner, not-a-fork, recent, copied-description — and differs ONLY on the
core structural signals. That makes the false-positive guard mean something.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from repo_impersonation_monitor.github_io import GitHubError
from repo_impersonation_monitor.models import Candidate, Project

from .pe_builder import build_pe

# Canonical "real project" identity that every case is scored against.
PROJECT_NAME = "acme-cli"
REAL_OWNER = "acme"
CANON_DESCRIPTION = "acme-cli - a fast command-line tool for ACME workflows."
SOURCE_MARKERS = ("src", "tests")
PROJECT_CREATED = datetime(2025, 1, 1, tzinfo=UTC)

# A release binary whose embedded version resources name an UNRELATED product —
# the differentiator tell of the evil twin.
DIFFERENT_PRODUCT_PE = {
    "ProductName": "TurboInstaller",
    "CompanyName": "Nimbus Labs",
    "OriginalFilename": "TurboInstaller-Setup.exe",
    "FileDescription": "Turbo Installer",
    "FileVersion": "9.9.9.9",
}


def real_project() -> Project:
    return Project(
        owner=REAL_OWNER,
        name=PROJECT_NAME,
        html_url=f"https://github.com/{REAL_OWNER}/{PROJECT_NAME}",
        created_at=PROJECT_CREATED,
        has_releases=False,
        source_dir_markers=SOURCE_MARKERS,
        description=CANON_DESCRIPTION,
    )


@dataclass(frozen=True)
class AssetSpec:
    name: str
    pe_fields: dict


@dataclass(frozen=True)
class SnapshotSpec:
    owner: str
    is_fork: bool
    created_at: datetime
    description: str | None
    top_level: tuple[str, ...]
    assets: tuple[AssetSpec, ...] = ()
    # Permutation cases override the repo name (a near-miss, not the exact name)
    # and the provenance tag; defaults keep the original exact-name cases intact.
    name: str | None = None
    discovered_via: str = "seeded"


class LocalSnapshotClient:
    """Serves a snapshot from disk with the methods signals.evaluate calls."""

    def __init__(self, snapshot_dir: Path, releases: list[dict]):
        self._tree_dir = snapshot_dir / "tree"
        self._assets_dir = snapshot_dir / "assets"
        self._releases = releases

    def get_tree_top_level(self, full_name, default_branch=None):
        return sorted(p.name for p in self._tree_dir.iterdir())

    def list_releases(self, full_name):
        return [dict(r) for r in self._releases]

    def download_asset(self, url, *, max_bytes):
        name = url.rsplit("/", 1)[-1]
        path = self._assets_dir / name
        if not path.exists():
            raise GitHubError(f"asset not found in snapshot: {name}")
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise GitHubError("asset exceeds size cap")
        return data


def _write_snapshot(base: Path, spec: SnapshotSpec) -> tuple[Candidate, LocalSnapshotClient]:
    snap = base / spec.owner
    tree = snap / "tree"
    assets = snap / "assets"
    tree.mkdir(parents=True)
    assets.mkdir(parents=True)

    for entry in spec.top_level:
        if "." in entry:  # treat as a file
            (tree / entry).write_text("synthetic\n")
        else:  # treat as a directory
            (tree / entry).mkdir()
            (tree / entry / ".keep").write_text("")

    release_assets = []
    for asset in spec.assets:
        data = build_pe(asset.pe_fields)
        (assets / asset.name).write_bytes(data)
        release_assets.append(
            {
                "name": asset.name,
                "browser_download_url": f"https://local.invalid/{asset.name}",
                "content_type": "application/x-msdownload",
                "size": len(data),
            }
        )
    releases = [{"assets": release_assets}] if release_assets else []

    cand_name = spec.name or PROJECT_NAME
    candidate = Candidate(
        owner=spec.owner,
        name=cand_name,
        html_url=f"https://github.com/{spec.owner}/{cand_name}",
        clone_url=f"https://github.com/{spec.owner}/{cand_name}.git",
        is_fork=spec.is_fork,
        created_at=spec.created_at,
        pushed_at=spec.created_at,
        discovered_via=spec.discovered_via,
        description=spec.description,
    )
    return candidate, LocalSnapshotClient(snap, releases)


# The three known-answer cases.
CASES: dict[str, SnapshotSpec] = {
    # Copied description, source tree STRIPPED, recent, NOT a fork, ships a binary
    # whose PE metadata names a different product. Expected: HIGH.
    "evil_twin": SnapshotSpec(
        owner="acme-cli",
        is_fork=False,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        description=CANON_DESCRIPTION,
        top_level=("README.md", "docs"),
        assets=(AssetSpec("acme-cli-setup.exe", DIFFERENT_PRODUCT_PE),),
    ),
    # Genuine fork: correct lineage, source intact, no hostile binary. Expected:
    # no flag (IGNORE/LOW); critically not_a_fork must NOT trigger.
    "legit_fork": SnapshotSpec(
        owner="contributor",
        is_fork=True,
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        description=CANON_DESCRIPTION,
        top_level=("README.md", "src", "tests"),
        assets=(),
    ),
    # The hard near-miss: full copy under a different owner, NOT a fork, recent,
    # description copied verbatim — shares every scary surface signal with the
    # evil twin — but source is intact and it ships no hostile binary. Expected:
    # must stay <= LOW (never MEDIUM/HIGH).
    "benign_mirror": SnapshotSpec(
        owner="mirror-host",
        is_fork=False,
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        description=CANON_DESCRIPTION,
        top_level=("README.md", "src", "tests", "docs"),
        assets=(),
    ),
    # Permutation-DISCOVERED impostor: a near-miss name (org-fold "acme-acme-cli")
    # under a different owner, carrying the SAME structural tells as the evil twin —
    # stripped source, mismatched-PE binary. The near-miss name is discovery's job;
    # the HIGH verdict must still rest on structure. Expected: HIGH.
    "permutation_evil": SnapshotSpec(
        owner="evilcorp",
        name="acme-acme-cli",
        discovered_via="permutation:org-fold",
        is_fork=False,
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        description=CANON_DESCRIPTION,
        top_level=("README.md", "docs"),
        assets=(AssetSpec("acme-cli-setup.exe", DIFFERENT_PRODUCT_PE),),
    ),
    # The false-positive guard for THIS feature: a legitimate near-name (affix
    # "acme-cli-ai") that shares hostile surface (different owner, not a fork, recent,
    # copied description) but keeps its source tree and ships no hostile binary.
    # Being permutation-discovered must NOT push it up — a near-miss NAME alone is
    # not a tell. Expected: <= LOW.
    "benign_near_name": SnapshotSpec(
        owner="community-ai",
        name="acme-cli-ai",
        discovered_via="permutation:affix",
        is_fork=False,
        created_at=datetime(2026, 5, 15, tzinfo=UTC),
        description=CANON_DESCRIPTION,
        top_level=("README.md", "src", "tests", "docs"),
        assets=(),
    ),
}


def build_case(base: Path, case: str) -> tuple[Project, Candidate, LocalSnapshotClient]:
    """Materialize a case into ``base`` and return (project, candidate, client)."""
    candidate, client = _write_snapshot(base, CASES[case])
    return real_project(), candidate, client
