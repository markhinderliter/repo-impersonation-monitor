"""Parse and validate Action inputs into a ``Config``.

This is a system boundary: validate hard, fail fast with clear messages. Inputs
arrive as ``INPUT_*`` environment variables (set explicitly by ``action.yml``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .models import ConfidenceTier

_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_DEFAULT_MAX_CANDIDATES = 100


class ConfigError(ValueError):
    """Raised when Action inputs are missing or invalid."""


@dataclass(frozen=True)
class Config:
    project_name: str
    project_repo: str
    project_owner: str
    real_html_url: str
    report_repo: str
    allowlist: frozenset[str]
    github_token: str
    min_tier_to_report: ConfidenceTier
    max_candidates: int
    dry_run: bool


def _get(env: Mapping[str, str], key: str, default: str = "") -> str:
    return (env.get(key) or default).strip()


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _parse_allowlist(raw: str) -> frozenset[str]:
    # Accept comma- or newline-separated owner/name entries; normalize for
    # case-insensitive matching against candidate full names.
    parts = re.split(r"[,\n]", raw)
    return frozenset(p.strip().lower() for p in parts if p.strip())


def load_config(env: Mapping[str, str]) -> Config:
    """Build a validated ``Config`` from environment inputs.

    Raises ``ConfigError`` on any missing/invalid input.
    """
    project_name = _get(env, "INPUT_PROJECT_NAME")
    if not project_name:
        raise ConfigError("project-name is required")

    project_repo = _get(env, "INPUT_PROJECT_REPO")
    if not _REPO_RE.match(project_repo):
        raise ConfigError(
            f"project-repo must be 'owner/name', got {project_repo!r}"
        )
    project_owner = project_repo.split("/", 1)[0]

    dry_run = _parse_bool(_get(env, "INPUT_DRY_RUN"))

    github_token = _get(env, "INPUT_GITHUB_TOKEN")
    if not github_token and not dry_run:
        raise ConfigError("github-token is required unless dry-run is enabled")

    real_html_url = _get(env, "INPUT_REAL_URL") or f"https://github.com/{project_repo}"

    report_repo = _get(env, "INPUT_REPORT_REPO") or project_repo
    if not _REPO_RE.match(report_repo):
        raise ConfigError(f"report-repo must be 'owner/name', got {report_repo!r}")

    min_tier_raw = _get(env, "INPUT_MIN_TIER") or "HIGH"
    try:
        min_tier = ConfidenceTier.from_name(min_tier_raw)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    max_raw = _get(env, "INPUT_MAX_CANDIDATES")
    if not max_raw:
        max_candidates = _DEFAULT_MAX_CANDIDATES
    else:
        try:
            max_candidates = int(max_raw)
        except ValueError as exc:
            raise ConfigError(f"max-candidates must be an integer, got {max_raw!r}") from exc
        if max_candidates <= 0:
            raise ConfigError(f"max-candidates must be positive, got {max_candidates}")

    return Config(
        project_name=project_name,
        project_repo=project_repo,
        project_owner=project_owner,
        real_html_url=real_html_url,
        report_repo=report_repo,
        allowlist=_parse_allowlist(_get(env, "INPUT_ALLOWLIST")),
        github_token=github_token,
        min_tier_to_report=min_tier,
        max_candidates=max_candidates,
        dry_run=dry_run,
    )
