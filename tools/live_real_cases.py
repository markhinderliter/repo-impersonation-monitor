"""Live, metadata-only triage pass over the real-cases ground-truth manifest.

RAILS (deliberate):
  - Network-gated: a standalone tool, never collected by pytest, so unit tests
    stay offline. Refuses to run unless RIM_LIVE=1 is set.
  - Metadata-only: scores using API metadata only (repo info, tree listing,
    releases listing). No release binary is ever downloaded or parsed
    (signals.evaluate(..., read_pe=False)).
  - Read-only: never opens issues. This is analysis, not action.

Comparison targets: a look-alike is scored against the real project it imitates;
a canonical/legitimate repo with no same-name rival is self-baselined (the
correct verdict for the rightful owner is "not flagged").

Usage:
    RIM_LIVE=1 GITHUB_TOKEN="$(gh auth token)" python tools/live_real_cases.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from repo_impersonation_monitor import scoring, signals
from repo_impersonation_monitor.config import load_config
from repo_impersonation_monitor.github_io import GitHubClient, GitHubError
from repo_impersonation_monitor.main import _parse_dt, _source_markers
from repo_impersonation_monitor.models import Candidate, Project

MANIFEST = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "real_cases.yaml"

# Real impersonation target for look-alike cases. Anything not listed is
# self-baselined (canonical/legitimate repo, no same-name rival).
COMPARE_TARGETS = {
    "bigdatasciencegroup/bytedance-deer-flow": "bytedance/deer-flow",
}


def fetch_project(gh: GitHubClient, repo: str) -> Project:
    info = gh.get_repo(repo)
    has_releases = bool(gh.list_releases(repo))
    dirs = gh.get_tree_top_level_dirs(repo)
    return Project(
        owner=info["owner"]["login"],
        name=info["name"],
        html_url=info["html_url"],
        created_at=_parse_dt(info["created_at"]),
        has_releases=has_releases,
        source_dir_markers=_source_markers(dirs),
        description=info.get("description"),
    )


def fetch_candidate(gh: GitHubClient, repo: str) -> Candidate:
    info = gh.get_repo(repo)
    return Candidate(
        owner=info["owner"]["login"],
        name=info["name"],
        html_url=info["html_url"],
        clone_url=info["clone_url"],
        is_fork=bool(info.get("fork", False)),
        created_at=_parse_dt(info["created_at"]),
        pushed_at=_parse_dt(info.get("pushed_at") or info["created_at"]),
        discovered_via="real-case",
        description=info.get("description"),
    )


def assess(expected: str, tier_name: str) -> str:
    flagged = tier_name == "HIGH"
    if expected == "do_not_flag":
        return "OK (not flagged)" if not flagged else "FALSE POSITIVE"
    return f"surfaced @ {tier_name}"  # candidate_to_classify — informational


def main() -> int:
    if os.environ.get("RIM_LIVE") != "1":
        print("Refusing to run: set RIM_LIVE=1 to confirm a live (network) pass.", file=sys.stderr)
        return 2
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print('Set GITHUB_TOKEN (e.g. GITHUB_TOKEN="$(gh auth token)").', file=sys.stderr)
        return 2

    gh = GitHubClient(token=token)
    cfg = load_config(
        {"INPUT_PROJECT_NAME": "x", "INPUT_PROJECT_REPO": "x/x", "INPUT_GITHUB_TOKEN": token}
    )
    cases = yaml.safe_load(MANIFEST.read_text())["cases"]

    print("repo | expected | compare_to | tier | score | triggered | assessment")
    print("---|---|---|---|---|---|---")
    for case in cases:
        repo = case["canonical_repo"]
        expected = case["expected_label"]
        target = COMPARE_TARGETS.get(repo, repo)
        try:
            project = fetch_project(gh, target)
            candidate = fetch_candidate(gh, repo)
            enriched, sig_results, pe = signals.evaluate(
                candidate, project, cfg, gh, read_pe=False
            )
            scored = scoring.score(enriched, tuple(sig_results), pe)
            triggered = ", ".join(s.key for s in scored.triggered_signals) or "-"
            print(
                f"{repo} | {expected} | {target} | {scored.tier.name} | "
                f"{scored.score:.2f} | {triggered} | {assess(expected, scored.tier.name)}"
            )
        except GitHubError as exc:
            print(f"{repo} | {expected} | {target} | ERROR | - | {exc} | could not evaluate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
