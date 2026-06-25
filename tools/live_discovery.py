"""First live pass of the DISCOVERY path: candidates.generate() over the network.

Sibling to ``live_real_cases.py``. Where that runner scores hand-picked repos from
the ground-truth manifest, THIS runner exercises discovery end to end: for each seed
project it runs ``candidates.generate()`` against the real GitHub Search API — the
same call ``main.run()`` makes — then scores every surfaced repo. Each seed section
answers two questions at once: what did discovery FIND, and how did the scorer JUDGE
each find.

RAILS (deliberate, the same posture as the manifest runner):
  - Network-gated: a standalone tool, never collected by pytest (so unit tests stay
    offline). Refuses to run unless ``RIM_LIVE=1`` is set.
  - Metadata-only: ``signals.evaluate(..., read_pe=False)`` — no release binary is
    ever downloaded or parsed this pass.
  - Read-only: never opens an issue, never forks or stars. Discovery + scoring only.
  - Rate-aware: a per-seed candidate cap (``RIM_MAX_CANDIDATES``, default 30) is
    handed to ``generate()``. If a seed comes back at the cap, that is reported as
    truncation — a finding about how popular the name is — not paged past.

Usage:
    RIM_LIVE=1 GITHUB_TOKEN="$(gh auth token)" python tools/live_discovery.py
"""

from __future__ import annotations

import os
import sys

from repo_impersonation_monitor import candidates, scoring, signals
from repo_impersonation_monitor.config import Config, load_config
from repo_impersonation_monitor.github_io import GitHubClient, GitHubError
from repo_impersonation_monitor.main import _build_project
from repo_impersonation_monitor.models import ConfidenceTier, ScoredCandidate

# Seeds: run discovery AS each project's maintainer. The name part drives the Search
# query (``"<name> in:name"``); the full owner/name is excluded from its own results.
SEEDS: tuple[str, ...] = (
    "heygen-com/hyperframes",
    "NousResearch/hermes-agent",
    "garrytan/gstack",
    "mattpocock/skills",
    "bytedance/deer-flow",
)

# Per-seed look-alike to check discovery for by name. deer-flow's look-alike,
# bigdatasciencegroup/bytedance-deer-flow, was VERIFIED a fork of bytedance/deer-flow
# (live check 2026-06-25), so GitHub excludes it from search by default — it is
# invisible to discovery regardless of name strategy. That is the weaponized-fork
# accepted gap (THREAT_MODEL §6), not a permutation / exact-name gap.
WATCH_LOOKALIKE: dict[str, str] = {
    "bytedance/deer-flow": "bigdatasciencegroup/bytedance-deer-flow",
}

DEFAULT_CAP = 30
_TIER_ORDER: tuple[ConfidenceTier, ...] = (
    ConfidenceTier.IGNORE,
    ConfidenceTier.LOW,
    ConfidenceTier.MEDIUM,
    ConfidenceTier.HIGH,
)


# --- pure helpers (no network; unit-tested in tests/test_live_discovery.py) -------

def row_for(scored: ScoredCandidate) -> tuple[str, str, str, str]:
    """One report row: (discovered_repo, discovered_via, tier, signals_fired)."""
    fired = ", ".join(s.key for s in scored.triggered_signals) or "-"
    return (
        scored.candidate.full_name,
        scored.candidate.discovered_via,
        scored.tier.name,
        fired,
    )


def tier_counts(scored: list[ScoredCandidate]) -> dict[str, int]:
    """Count surfaced candidates by tier name, every tier present (zero-filled)."""
    counts = {tier.name: 0 for tier in _TIER_ORDER}
    for sc in scored:
        counts[sc.tier.name] += 1
    return counts


def is_truncated(surfaced: int, cap: int) -> bool:
    """A seed that comes back at (or above) the cap was truncated: more exist."""
    return surfaced >= cap


def elevated(scored: list[ScoredCandidate]) -> list[ScoredCandidate]:
    """Surfaced repos scoring above LOW — the noise-floor repos to eyeball."""
    return [sc for sc in scored if sc.tier >= ConfidenceTier.MEDIUM]


def lookalike_surfaced(scored: list[ScoredCandidate], full_name: str) -> bool:
    """Did discovery surface this exact owner/name on its own?"""
    target = full_name.lower()
    return any(sc.candidate.full_name.lower() == target for sc in scored)


def render_counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{tier.name}={counts[tier.name]}" for tier in _TIER_ORDER)


def render_seed_section(seed: str, scored: list[ScoredCandidate], cap: int) -> str:
    """Render one seed's fused FOUND + JUDGED report block as Markdown."""
    surfaced = len(scored)
    lines = [
        f"### Seed: `{seed}`",
        "",
        f"- **Total surfaced:** {surfaced}",
        f"- **By tier:** {render_counts(tier_counts(scored))}",
    ]
    if is_truncated(surfaced, cap):
        lines.append(
            f"- **⚠ Truncated at cap ({cap}):** more same-name repos exist and were "
            "not scored. The cap-hit is itself a finding about this name's popularity."
        )
    lines += [
        "",
        "| discovered_repo | discovered_via | tier | signals_fired |",
        "|---|---|---|---|",
    ]
    if scored:
        for repo, via, tier, fired in (row_for(sc) for sc in scored):
            lines.append(f"| {repo} | {via} | {tier} | {fired} |")
    else:
        lines.append("| _(none surfaced)_ | - | - | - |")
    lines.append("")

    # Watch-item 1: look-alike membership (e.g. the deer-flow case).
    watch = WATCH_LOOKALIKE.get(seed)
    if watch:
        if lookalike_surfaced(scored, watch):
            lines.append(
                f"- **Look-alike check:** `{watch}` WAS surfaced by discovery on its own."
            )
        else:
            lines.append(
                f"- **Look-alike check:** `{watch}` was NOT surfaced — and that is "
                "expected: it is a fork of the real project, so GitHub excludes it "
                "from search by default. A fork-class look-alike is invisible to "
                "discovery regardless of name strategy (exact-name or permutation) — "
                "the weaponized-fork gap (THREAT_MODEL §6), not a permutation gap."
            )

    # Watch-item 2: noise floor.
    elev = elevated(scored)
    if elev:
        names = ", ".join(f"`{sc.candidate.full_name}` ({sc.tier.name})" for sc in elev)
        lines.append(
            f"- **🚩 Noise floor:** {len(elev)} surfaced repo(s) scored above LOW and "
            f"warrant a human look: {names}."
        )
    else:
        lines.append(
            "- **Noise floor:** every surfaced repo scored IGNORE/LOW — the scorer "
            "tiered the name-twins down correctly."
        )
    lines.append("")
    return "\n".join(lines)


# --- live orchestration (network) -------------------------------------------------

def seed_config(seed: str, token: str, cap: int) -> Config:
    """Build the per-seed Config exactly as the Action would for that maintainer."""
    name = seed.split("/", 1)[1]
    return load_config(
        {
            "INPUT_PROJECT_NAME": name,
            "INPUT_PROJECT_REPO": seed,
            "INPUT_GITHUB_TOKEN": token,
            "INPUT_MAX_CANDIDATES": str(cap),
            "INPUT_DRY_RUN": "true",
        }
    )


def discover_and_score(gh: GitHubClient, cfg: Config) -> list[ScoredCandidate]:
    """Run the discovery half of main.run(): generate() then score each find."""
    project = _build_project(cfg, gh)
    found = candidates.generate(cfg, gh)  # the real, live Search API discovery call
    scored: list[ScoredCandidate] = []
    for cand in found:
        enriched, sig_results, pe = signals.evaluate(cand, project, cfg, gh, read_pe=False)
        scored.append(scoring.score(enriched, tuple(sig_results), pe))
    return scored


def cap_from_env() -> int:
    raw = os.environ.get("RIM_MAX_CANDIDATES", "").strip()
    if not raw:
        return DEFAULT_CAP
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CAP
    return value if value > 0 else DEFAULT_CAP


def main() -> int:
    if os.environ.get("RIM_LIVE") != "1":
        print("Refusing to run: set RIM_LIVE=1 to confirm a live (network) pass.", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print('Set GITHUB_TOKEN (e.g. GITHUB_TOKEN="$(gh auth token)").', file=sys.stderr)
        return 2

    cap = cap_from_env()
    gh = GitHubClient(token=token)

    print("# Live discovery pass — candidates.generate() over the network")
    print()
    print(
        "Discovery is exercised end to end (generate() against the real Search API), "
        f"metadata-only (no binaries read), read-only (no issues opened), per-seed "
        f"cap = {cap}. Each section: what discovery FOUND and how it JUDGED each find."
    )
    print()

    exit_code = 0
    for seed in SEEDS:
        try:
            cfg = seed_config(seed, token, cap)
            scored = discover_and_score(gh, cfg)
            print(render_seed_section(seed, scored, cap))
        except GitHubError as exc:
            exit_code = 1
            print(f"### Seed: `{seed}`\n\n- **ERROR:** could not complete discovery: {exc}\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
