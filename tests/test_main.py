"""End-to-end orchestration tests with a fully faked GitHub client."""

import pytest

from repo_impersonation_monitor import main as main_mod
from repo_impersonation_monitor.config import ConfigError
from repo_impersonation_monitor.github_io import GitHubError
from repo_impersonation_monitor.main import RunSummary, main, run


def base_env(**overrides):
    env = {
        "INPUT_PROJECT_NAME": "MyProject",
        "INPUT_PROJECT_REPO": "realowner/MyProject",
        "INPUT_GITHUB_TOKEN": "tok",
    }
    env.update(overrides)
    return env


def repo_dict(full_name, created="2025-01-01T00:00:00Z", desc="The real project", branch="main"):
    owner, name = full_name.split("/")
    return {
        "owner": {"login": owner},
        "name": name,
        "full_name": full_name,
        "html_url": f"https://github.com/{full_name}",
        "created_at": created,
        "description": desc,
        "default_branch": branch,
    }


def search_item(owner, name, *, created="2026-06-22T10:42:32Z", desc="The real project"):
    full = f"{owner}/{name}"
    return {
        "name": name, "full_name": full, "owner": {"login": owner},
        "html_url": f"https://github.com/{full}",
        "clone_url": f"https://github.com/{full}.git",
        "fork": False, "created_at": created, "pushed_at": created,
        "description": desc,
    }


def release(*names):
    return {"assets": [
        {"name": n, "browser_download_url": f"https://x/{n}", "content_type": "x", "size": 10}
        for n in names
    ]}


class FakeGitHub:
    """Models every client method main() touches, keyed by repo full_name."""

    def __init__(self, *, real_repo, real_dirs, real_releases, search_items,
                 candidate_trees=None, candidate_releases=None, asset_bytes=b"",
                 existing_issue=None):
        self._real_repo = real_repo
        self._real_dirs = real_dirs
        self._real_releases = real_releases
        self._search_items = search_items
        self._candidate_trees = candidate_trees or {}
        self._candidate_releases = candidate_releases or {}
        self._asset_bytes = asset_bytes
        self._existing_issue = existing_issue
        self.opened = []

    def get_repo(self, full_name):
        return self._real_repo  # only the real project is fetched via get_repo

    def get_tree_top_level_dirs(self, full_name, default_branch=None):
        return list(self._real_dirs)

    def search_repos(self, query, *, per_page=100, max_pages=10):
        return list(self._search_items)

    def list_releases(self, full_name):
        if full_name == self._real_repo["full_name"]:
            return list(self._real_releases)
        return list(self._candidate_releases.get(full_name, []))

    def get_tree_top_level(self, full_name, default_branch=None):
        return list(self._candidate_trees.get(full_name, []))

    def download_asset(self, url, *, max_bytes):
        return self._asset_bytes

    def find_existing_issue(self, repo, marker, **kw):
        return self._existing_issue

    def open_issue(self, repo, title, body, labels=None):
        issue = {"number": len(self.opened) + 1, "html_url": "u", "repo": repo,
                 "title": title, "labels": labels, "body": body}
        self.opened.append(issue)
        return issue


def high_impostor_gh(**over):
    """A fake where evil/MyProject is a clear HIGH-tier impostor."""
    defaults = dict(
        real_repo=repo_dict("realowner/MyProject"),
        real_dirs=["src", "tests"],
        real_releases=[],  # real ships no binaries
        search_items=[search_item("evil", "MyProject")],
        candidate_trees={"evil/MyProject": ["README.md", "lib"]},  # stripped: no src/tests
        candidate_releases={"evil/MyProject": [release("MyProject-Setup.exe")]},
        asset_bytes=__import__("pathlib").Path(
            __file__).parent.joinpath("fixtures/pe_mismatch.exe").read_bytes(),
    )
    defaults.update(over)
    return FakeGitHub(**defaults)


# --- happy path -----------------------------------------------------------

def test_high_impostor_opens_issue():
    gh = high_impostor_gh()
    summary = run(base_env(), gh=gh)
    assert summary.tier_counts.get("HIGH") == 1
    assert summary.issues_opened == 1
    assert len(gh.opened) == 1
    issue = gh.opened[0]
    assert issue["repo"] == "realowner/MyProject"
    assert "evil/MyProject" in issue["title"]
    assert "never executed" in issue["body"].lower()
    assert "Janus Key" in issue["body"]  # PE mismatch surfaced


def test_dry_run_opens_no_issues():
    gh = high_impostor_gh()
    summary = run(base_env(INPUT_DRY_RUN="true"), gh=gh)
    assert summary.dry_run is True
    assert summary.tier_counts.get("HIGH") == 1
    assert summary.issues_opened == 0
    assert gh.opened == []


def test_dedupe_skips_existing_issue():
    gh = high_impostor_gh(existing_issue={"number": 9, "html_url": "old"})
    summary = run(base_env(), gh=gh)
    assert summary.issues_opened == 0
    assert summary.issues_deduped == 1
    assert gh.opened == []


def test_min_tier_filters_out_lower_tiers():
    # No binary, source present -> only weak signals -> LOW, below default HIGH.
    gh = FakeGitHub(
        real_repo=repo_dict("realowner/MyProject"),
        real_dirs=["src"],
        real_releases=[],
        search_items=[search_item("meh", "MyProject")],
        candidate_trees={"meh/MyProject": ["src", "README.md"]},
        candidate_releases={},
    )
    summary = run(base_env(), gh=gh)
    assert summary.issues_opened == 0
    assert summary.tier_counts.get("HIGH", 0) == 0


def test_summary_counts_all_candidates():
    gh = high_impostor_gh(search_items=[
        search_item("evil", "MyProject"),
        search_item("alsofake", "MyProject"),
    ], candidate_trees={
        "evil/MyProject": ["README.md", "lib"],
        "alsofake/MyProject": ["src"],  # keeps source -> lower tier
    })
    summary = run(base_env(), gh=gh)
    assert summary.candidates == 2


def test_lower_min_tier_reports_medium():
    gh = FakeGitHub(
        real_repo=repo_dict("realowner/MyProject"),
        real_dirs=["src"],
        real_releases=[],
        search_items=[search_item("evil", "MyProject")],
        candidate_trees={"evil/MyProject": ["src", "README.md"]},  # source kept
        candidate_releases={"evil/MyProject": [release("Setup.exe")]},  # but ships binary
    )
    summary = run(base_env(INPUT_MIN_TIER="MEDIUM"), gh=gh)
    assert summary.issues_opened >= 1


# --- error handling -------------------------------------------------------

def test_config_error_propagates():
    env = base_env()
    del env["INPUT_PROJECT_NAME"]
    with pytest.raises(ConfigError):
        run(env, gh=high_impostor_gh())


def test_report_repo_override_targets_other_repo():
    gh = high_impostor_gh()
    run(base_env(INPUT_REPORT_REPO="realowner/security"), gh=gh)
    assert gh.opened[0]["repo"] == "realowner/security"


def test_real_project_marker_derivation_excludes_non_source_dirs(monkeypatch):
    # Real repo has assets/docs alongside src; markers must drop the non-source.
    gh = high_impostor_gh(
        real_dirs=["src", "assets", "docs", ".github"],
        candidate_trees={"evil/MyProject": ["assets", "README.md"]},  # kept assets, no src
    )
    summary = run(base_env(), gh=gh)
    # Because 'assets' is NOT a source marker, stripped-source still triggers.
    assert summary.tier_counts.get("HIGH") == 1


def test_run_builds_default_client_when_none(monkeypatch):
    gh = high_impostor_gh()
    monkeypatch.setattr(main_mod, "GitHubClient", lambda token: gh)
    summary = run(base_env(), gh=None)  # exercises the default-client branch
    assert summary.issues_opened == 1


# --- main() entrypoint ----------------------------------------------------

def test_main_returns_zero_on_success(monkeypatch):
    monkeypatch.setattr(main_mod, "run", lambda env: RunSummary())
    assert main() == 0


def test_main_returns_one_on_config_error(monkeypatch):
    def boom(env):
        raise ConfigError("bad input")

    monkeypatch.setattr(main_mod, "run", boom)
    assert main() == 1


def test_main_returns_one_on_github_error(monkeypatch):
    def boom(env):
        raise GitHubError("api down")

    monkeypatch.setattr(main_mod, "run", boom)
    assert main() == 1
