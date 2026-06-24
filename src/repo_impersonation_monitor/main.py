"""Entry point: wire Action inputs -> pipeline -> issue.

main() reads the environment, runs the pipeline, prints a summary, and returns
an exit code. run() holds the orchestration and accepts an injected client so it
is testable without network access.

Nothing is filed automatically beyond opening a draft *issue* in the
maintainer's own repo: the issue contains a report for the maintainer to review
and, if appropriate, submit. dry-run files nothing at all.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

from . import candidates, report, scoring, signals
from .config import Config, ConfigError, load_config
from .github_io import GitHubClient, GitHubError
from .models import Project

logger = logging.getLogger("repo_impersonation_monitor")

ISSUE_LABELS = ("impersonation-report",)

# Top-level directories that are not "source" — a clone keeping these does not
# disprove a stripped source tree, so they must not count as source markers.
_NON_SOURCE_DIRS = frozenset(
    {
        ".github", ".git", ".vscode", ".idea", "docs", "doc", "assets", "asset",
        "images", "image", "img", "screenshots", "media", "examples", "example",
        "dist", "build", "node_modules", "public", "static", ".devcontainer",
    }
)


@dataclass
class RunSummary:
    candidates: int = 0
    tier_counts: dict[str, int] = field(default_factory=dict)
    issues_opened: int = 0
    issues_deduped: int = 0
    dry_run: bool = False
    reported: list[str] = field(default_factory=list)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _source_markers(dirs: list[str]) -> tuple[str, ...]:
    return tuple(d for d in dirs if d.lower() not in _NON_SOURCE_DIRS)


def _build_project(config: Config, gh: GitHubClient) -> Project:
    repo = gh.get_repo(config.project_repo)
    has_releases = bool(gh.list_releases(config.project_repo))
    dirs = gh.get_tree_top_level_dirs(config.project_repo)
    return Project(
        owner=repo["owner"]["login"],
        name=repo["name"],
        html_url=repo.get("html_url", config.real_html_url),
        created_at=_parse_dt(repo["created_at"]),
        has_releases=has_releases,
        source_dir_markers=_source_markers(dirs),
        description=repo.get("description"),
    )


def run(env, gh: GitHubClient | None = None) -> RunSummary:
    """Run the full detection pipeline. Returns a summary of what happened."""
    config = load_config(env)
    if gh is None:
        gh = GitHubClient(token=config.github_token)

    project = _build_project(config, gh)
    found = candidates.generate(config, gh)

    summary = RunSummary(candidates=len(found), dry_run=config.dry_run)
    logger.info("Evaluating %d candidate(s) for %s", len(found), project.full_name)

    for candidate in found:
        enriched, sig_results, pe_metadata = signals.evaluate(candidate, project, config, gh)
        scored = scoring.score(enriched, tuple(sig_results), pe_metadata)
        tier = scored.tier
        summary.tier_counts[tier.name] = summary.tier_counts.get(tier.name, 0) + 1

        if tier < config.min_tier_to_report:
            continue
        _handle_report(scored, project, config, gh, summary)

    _log_summary(summary)
    return summary


def _handle_report(scored, project, config, gh, summary: RunSummary) -> None:
    rendered = report.render(scored, project, config)
    marker = report.dedupe_marker(scored.candidate)
    full_name = scored.candidate.full_name

    if config.dry_run:
        logger.info("[dry-run] would open issue for %s (%s)", full_name, scored.tier.name)
        summary.reported.append(full_name)
        return

    existing = gh.find_existing_issue(config.report_repo, marker)
    if existing:
        logger.info("Existing issue found for %s — skipping", full_name)
        summary.issues_deduped += 1
        return

    issue = gh.open_issue(
        config.report_repo, rendered.title, rendered.body, labels=list(ISSUE_LABELS)
    )
    summary.issues_opened += 1
    summary.reported.append(full_name)
    logger.info("Opened issue #%s for %s", issue.get("number"), full_name)


def _log_summary(summary: RunSummary) -> None:
    tiers = ", ".join(f"{k}={v}" for k, v in sorted(summary.tier_counts.items())) or "none"
    logger.info(
        "Done: %d candidates [%s]; issues opened=%d, deduped=%d%s",
        summary.candidates,
        tiers,
        summary.issues_opened,
        summary.issues_deduped,
        " (dry-run)" if summary.dry_run else "",
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    try:
        run(os.environ)
    except ConfigError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except GitHubError as exc:
        logger.error("GitHub API error: %s", exc)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
