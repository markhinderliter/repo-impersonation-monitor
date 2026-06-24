"""Candidate generation: find repos that might be impersonating the project.

MVP generators (from one search query against the ~30/min search bucket):

- **exact-name**: a repo whose name equals the project name under a different
  owner.
- **bare-org / vanity-org**: the ``Name/Name`` trick where the name is reused as
  both owner and repo. GitHub search has no ``owner==repo`` qualifier, so this is
  a post-filter over the same exact-name results.

Forks are intentionally NOT filtered out: ``not_a_fork`` is a scored signal, so a
legitimate fork stays visible but scores low (it keeps its source tree and is a
fork), which is the desired false-positive behavior.

Future generators (not built): dnstwist-style permutations, README text search.
"""

from __future__ import annotations

from datetime import datetime

from .config import Config
from .github_io import GitHubClient
from .models import Candidate

_SEARCH_PER_PAGE = 100
_MAX_SEARCH_PAGES = 10  # GitHub caps search at 1000 results (10 * 100) anyway


def exact_name_query(name: str) -> str:
    """Build the repo-search query for the project name."""
    return f"{name} in:name"


def _parse_dt(value: str) -> datetime:
    # GitHub timestamps look like "2026-06-22T10:42:32Z"; fromisoformat handles
    # the trailing Z on Python 3.11+.
    return datetime.fromisoformat(value)


def item_to_candidate(item: dict, discovered_via: str) -> Candidate:
    """Map a GitHub search-result dict to a Candidate."""
    created = item["created_at"]
    return Candidate(
        owner=item["owner"]["login"],
        name=item["name"],
        html_url=item["html_url"],
        clone_url=item["clone_url"],
        is_fork=bool(item.get("fork", False)),
        created_at=_parse_dt(created),
        pushed_at=_parse_dt(item.get("pushed_at") or created),
        discovered_via=discovered_via,
        description=item.get("description"),
    )


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Drop duplicate full names, preserving first-seen order."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for cand in candidates:
        key = cand.full_name.lower()
        if key not in seen:
            seen.add(key)
            out.append(cand)
    return out


def exclude_self_and_allowlist(candidates: list[Candidate], config: Config) -> list[Candidate]:
    """Remove the real repo and any allowlisted full names (case-insensitive)."""
    excluded = {config.project_repo.lower()} | set(config.allowlist)
    return [c for c in candidates if c.full_name.lower() not in excluded]


def cap(candidates: list[Candidate], limit: int) -> list[Candidate]:
    """Bound the candidate list to stay within downstream rate budget."""
    return candidates[:limit]


def _max_pages_for(max_candidates: int) -> int:
    pages = (max_candidates + _SEARCH_PER_PAGE - 1) // _SEARCH_PER_PAGE
    return max(1, min(_MAX_SEARCH_PAGES, pages))


def generate(config: Config, gh: GitHubClient) -> list[Candidate]:
    """Generate impersonation candidates for the configured project."""
    project_name = config.project_name.lower()
    items = gh.search_repos(
        exact_name_query(config.project_name),
        per_page=_SEARCH_PER_PAGE,
        max_pages=_max_pages_for(config.max_candidates),
    )

    candidates: list[Candidate] = []
    for item in items:
        name = item.get("name", "")
        owner = item.get("owner", {}).get("login", "")
        if name.lower() != project_name:
            continue  # fuzzy search noise — keep only exact-name matches
        discovered_via = "bare-org" if owner.lower() == name.lower() else "exact-name"
        candidates.append(item_to_candidate(item, discovered_via))

    candidates = dedupe(candidates)
    candidates = exclude_self_and_allowlist(candidates, config)
    return cap(candidates, config.max_candidates)
