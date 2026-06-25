"""Candidate generation: find repos that might be impersonating the project.

Generators (each is one or more search queries against the ~30/min bucket):

- **exact-name**: a repo whose name equals the project name under a different
  owner.
- **bare-org / vanity-org**: the ``Name/Name`` trick where the name is reused as
  both owner and repo. GitHub search has no ``owner==repo`` qualifier, so this is
  a post-filter over the same exact-name results.
- **permutation** (near-miss names): targeted queries for generated variants of
  the project name — separator swaps (``deer-flow`` -> ``deerflow``), owner+name
  "org-folding" (``owner/name`` -> ``owner-name``), and a curated set of common
  affixes. Each variant is its own ``"{variant} in:name"`` query, sorted
  by recency (``sort=updated``) because the permutation target is a *fresh*
  impostor that best-match would bury. Results are kept only on an exact match to
  the variant. ``discovered_via`` is tagged per generator.

Forks are excluded from search by GitHub's default, so the population here is
already non-fork; ``not_a_fork`` is a scored signal regardless. Unicode-homoglyph
variants are impossible — GitHub identifiers are ASCII — so that dnstwist class is
intentionally absent (not a gap). Single-edit typo variants are deferred
(combinatorial cost, low yield).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from .config import Config
from .github_io import GitHubClient
from .models import Candidate

logger = logging.getLogger("repo_impersonation_monitor")

_SEARCH_PER_PAGE = 100
_MAX_SEARCH_PAGES = 10  # GitHub caps search at 1000 results (10 * 100) anyway

# Separators that GitHub repo names allow (ASCII only). "" is the run-together form.
_SEPARATORS = ("-", "_", ".", "")
# Curated, high-yield affixes. Kept tight on purpose: every entry is one more query
# against the ~30/min bucket, and affixes are the lowest-confidence variant class.
_AFFIXES = ("ai", "cli", "app", "js", "py", "official", "pro")
_TOKEN_SPLIT_RE = re.compile(r"[-_.]+")


def exact_name_query(name: str) -> str:
    """Build the repo-search query for a name (also used per permutation variant)."""
    return f"{name} in:name"


def name_variants(owner: str, name: str) -> list[tuple[str, str]]:
    """Generate near-miss name variants as ``(variant, discovered_via)`` pairs.

    Ordered by confidence so a downstream cap truncates the weakest first:
    org-folding, then separator swaps, then affixes. Lower-cased, deduped, and the
    exact project name is never emitted (that is the exact-name generator's job).
    """
    base = name.lower()
    owner_l = owner.lower()
    seen = {base}
    out: list[tuple[str, str]] = []

    def add(variant: str, via: str) -> None:
        v = variant.lower()
        if v and v not in seen:
            seen.add(v)
            out.append((v, via))

    # 1. Org-folding (highest confidence; the confirmed case). owner+sep+name.
    for sep in _SEPARATORS:
        add(f"{owner_l}{sep}{base}", "permutation:org-fold")

    # 2. Separator swaps (only meaningful for multi-token names).
    tokens = [t for t in _TOKEN_SPLIT_RE.split(base) if t]
    if len(tokens) > 1:
        for sep in _SEPARATORS:
            add(sep.join(tokens), "permutation:separator")

    # 3. Common affixes, suffix and prefix, single "-" join.
    for affix in _AFFIXES:
        add(f"{base}-{affix}", "permutation:affix")
        add(f"{affix}-{base}", "permutation:affix")

    return out


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


def _exact_name_candidates(config: Config, gh: GitHubClient) -> list[Candidate]:
    """Exact-name + bare-org generator (the original path, behavior unchanged)."""
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

    candidates = exclude_self_and_allowlist(dedupe(candidates), config)
    return cap(candidates, config.max_candidates)


def _permutation_candidates(config: Config, gh: GitHubClient) -> list[Candidate]:
    """Permutation generator: one recency-sorted query per capped variant."""
    variants = name_variants(config.project_owner, config.project_name)
    if len(variants) > config.max_variants:
        logger.info(
            "Permutation variants truncated: %d generated, cap=%d "
            "(org-folding retained first)",
            len(variants),
            config.max_variants,
        )
        variants = variants[: config.max_variants]

    candidates: list[Candidate] = []
    for variant, discovered_via in variants:
        # Recency sort: the target is a fresh impostor that best-match would bury.
        items = gh.search_repos(
            exact_name_query(variant), per_page=_SEARCH_PER_PAGE, max_pages=1, sort="updated"
        )
        for item in items:
            if item.get("name", "").lower() != variant:
                continue  # keep only an exact match to the generated variant
            candidates.append(item_to_candidate(item, discovered_via))

    candidates = exclude_self_and_allowlist(dedupe(candidates), config)
    return cap(candidates, config.max_candidates)


def generate(config: Config, gh: GitHubClient) -> list[Candidate]:
    """Generate impersonation candidates: exact-name + bare-org, then permutations.

    Results merge with provenance precedence — exact-name first, then permutation
    generators in confidence order — so a repo found two ways keeps its
    higher-confidence ``discovered_via`` tag. Deduped by full ``owner/name``.
    """
    exact = _exact_name_candidates(config, gh)
    permutations = _permutation_candidates(config, gh)
    return dedupe(exact + permutations)
