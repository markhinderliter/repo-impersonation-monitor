"""Tests for individual structural signals and the evaluate() enrichment flow."""

from datetime import UTC, datetime
from pathlib import Path

from repo_impersonation_monitor import scoring, signals
from repo_impersonation_monitor.config import load_config
from repo_impersonation_monitor.github_io import GitHubError, NotFoundError
from repo_impersonation_monitor.models import Candidate, PeMetadata, Project

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(**overrides):
    env = {
        "INPUT_PROJECT_NAME": "MyProject",
        "INPUT_PROJECT_REPO": "realowner/MyProject",
        "INPUT_GITHUB_TOKEN": "tok",
    }
    env.update(overrides)
    return load_config(env)


def make_project(**kw):
    defaults = dict(
        owner="realowner", name="MyProject",
        html_url="https://github.com/realowner/MyProject",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
        has_releases=False, source_dir_markers=("src", "tests"),
        description="A real project that does real things",
    )
    defaults.update(kw)
    return Project(**defaults)


def make_candidate(**kw):
    ts = datetime(2026, 6, 22, tzinfo=UTC)
    defaults = dict(
        owner="evil", name="MyProject",
        html_url="https://github.com/evil/MyProject",
        clone_url="https://github.com/evil/MyProject.git",
        is_fork=False, created_at=ts, pushed_at=ts, discovered_via="exact-name",
        description="A real project that does real things",
    )
    defaults.update(kw)
    return Candidate(**defaults)


class FakeGitHub:
    def __init__(self, *, tree=None, releases=None, asset_bytes=b"",
                 tree_error=None, release_error=None, download_error=None):
        self._tree = tree
        self._releases = releases or []
        self._asset_bytes = asset_bytes
        self._tree_error = tree_error
        self._release_error = release_error
        self._download_error = download_error
        self.downloaded = []

    def get_tree_top_level(self, full_name, default_branch=None):
        if self._tree_error:
            raise self._tree_error
        return list(self._tree or [])

    def list_releases(self, full_name):
        if self._release_error:
            raise self._release_error
        return list(self._releases)

    def download_asset(self, url, *, max_bytes):
        self.downloaded.append(url)
        if self._download_error:
            raise self._download_error
        return self._asset_bytes


def release(*asset_specs):
    return {"assets": [
        {"name": n, "browser_download_url": f"https://x/{n}",
         "content_type": ct, "size": sz}
        for (n, ct, sz) in asset_specs
    ]}


def assets(*asset_specs):
    """Build ReleaseAsset tuple from (name, content_type, size) specs."""
    return signals._assets_from_releases([release(*asset_specs)])


# --- individual signals ---------------------------------------------------

def test_owner_mismatch_triggers_for_different_owner():
    r = signals.signal_owner_mismatch(make_candidate(owner="evil"), make_project())
    assert r.triggered is True
    assert r.weight == scoring.SIGNAL_WEIGHTS["owner_mismatch"]


def test_owner_mismatch_not_triggered_when_same_owner():
    r = signals.signal_owner_mismatch(make_candidate(owner="realowner"), make_project())
    assert r.triggered is False


def test_not_a_fork_triggers_when_not_fork():
    assert signals.signal_not_a_fork(make_candidate(is_fork=False)).triggered is True
    assert signals.signal_not_a_fork(make_candidate(is_fork=True)).triggered is False


def test_created_after_real_triggers():
    assert signals.signal_created_after_real(make_candidate(), make_project()).triggered is True
    older = make_candidate(created_at=datetime(2024, 1, 1, tzinfo=UTC))
    assert signals.signal_created_after_real(older, make_project()).triggered is False


def test_source_tree_stripped_triggers_when_markers_absent_and_desc_copied():
    cand = make_candidate(top_level_paths=("README.md", "lib", "assets"))
    r = signals.signal_source_tree_stripped(cand, make_project())
    assert r.triggered is True


def test_source_tree_not_stripped_when_marker_present():
    cand = make_candidate(top_level_paths=("src", "README.md"))
    assert signals.signal_source_tree_stripped(cand, make_project()).triggered is False


def test_source_tree_not_stripped_when_description_differs():
    # Same name but a genuinely different description -> not a copy -> conservative.
    cand = make_candidate(top_level_paths=("docs",), description="totally different thing")
    assert signals.signal_source_tree_stripped(cand, make_project()).triggered is False


def test_source_tree_stripped_skips_when_tree_unknown():
    cand = make_candidate(top_level_paths=None)
    assert signals.signal_source_tree_stripped(cand, make_project()).triggered is False


def pe_sig(pe):
    return signals.signal_pe_metadata_mismatch(make_candidate(), make_project(), pe)


def test_ships_binary_triggers_when_real_has_none():
    cand = make_candidate(has_releases=True, release_assets=assets(("a.exe", "x", 1)))
    r = signals.signal_ships_binary_real_does_not(cand, make_project(has_releases=False))
    assert r.triggered is True


def test_ships_binary_not_triggered_when_real_also_ships():
    cand = make_candidate(has_releases=True, release_assets=assets(("a.exe", "x", 1)))
    real_ships = make_project(has_releases=True)
    assert signals.signal_ships_binary_real_does_not(cand, real_ships).triggered is False


def test_platform_inconsistency_windows_binary_mac_description():
    cand = make_candidate(
        description="A native macOS productivity app",
        release_assets=assets(("Setup.exe", "x", 1)),
    )
    assert signals.signal_platform_path_inconsistency(cand, make_project()).triggered is True


def test_platform_inconsistency_not_triggered_when_windows_declared():
    cand = make_candidate(
        description="A Windows desktop app",
        release_assets=assets(("Setup.exe", "x", 1)),
    )
    assert signals.signal_platform_path_inconsistency(cand, make_project()).triggered is False


def test_pe_mismatch_triggers_on_unrelated_product_name():
    pe = PeMetadata(product_name="Janus Key", company_name="Duality Solutions", parse_ok=True)
    r = pe_sig(pe)
    assert r.triggered is True
    assert "Janus Key" in r.detail


def test_pe_mismatch_not_triggered_when_name_relates():
    pe = PeMetadata(product_name="MyProject", company_name="MyProject Authors", parse_ok=True)
    assert pe_sig(pe).triggered is False


def test_pe_mismatch_not_triggered_when_no_pe():
    assert pe_sig(None).triggered is False


def test_pe_mismatch_not_triggered_when_parse_failed():
    assert pe_sig(PeMetadata(parse_ok=False, parse_error="bad")).triggered is False


def test_pe_mismatch_not_triggered_when_fields_empty():
    # parsed OK but no ProductName/CompanyName -> nothing to compare.
    assert pe_sig(PeMetadata(parse_ok=True)).triggered is False


def test_names_relate_handles_empty():
    assert signals._names_relate("", "MyProject") is False
    assert signals._names_relate("MyProject", "") is False
    assert signals._names_relate("MyProject Tool", "MyProject") is True


def test_descriptions_match_handles_empty():
    cand = make_candidate(description=None)
    assert signals._descriptions_match(cand, make_project()) is False


# --- evaluate() integration ----------------------------------------------

def test_evaluate_enriches_and_runs_all_signals():
    gh = FakeGitHub(
        tree=["README.md", "lib", "assets"],
        releases=[release(("MyProject-Setup.exe", "application/x-msdownload", 1536))],
        asset_bytes=(FIXTURES / "pe_mismatch.exe").read_bytes(),
    )
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    keys = {s.key for s in sigs}
    # all structural signals are evaluated (behavioral churn signals are out of MVP)
    assert keys.issuperset(
        {"owner_mismatch", "not_a_fork", "created_after_real",
         "source_tree_stripped", "ships_binary_real_does_not",
         "platform_path_inconsistency", "pe_metadata_mismatch"}
    )
    # enriched
    assert cand.top_level_paths == ("README.md", "lib", "assets")
    assert cand.has_releases is True
    assert pe is not None and pe.product_name == "Janus Key"
    # the PE asset was downloaded for parsing
    assert gh.downloaded


def test_evaluate_full_pipeline_scores_high_for_real_impostor():
    gh = FakeGitHub(
        tree=["README.md", "lib"],
        releases=[release(("MyProject-Setup.exe", "x", 1536))],
        asset_bytes=(FIXTURES / "pe_mismatch.exe").read_bytes(),
    )
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    scored = scoring.score(cand, tuple(sigs), pe)
    assert scored.tier.name == "HIGH"


def test_evaluate_handles_missing_tree_gracefully():
    gh = FakeGitHub(tree_error=NotFoundError("no tree"), releases=[])
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    assert cand.top_level_paths is None
    # stripped-source signal should simply not trigger
    stripped = next(s for s in sigs if s.key == "source_tree_stripped")
    assert stripped.triggered is False


def test_evaluate_skips_pe_when_only_archive_asset():
    # The real-world .7z case: no direct PE asset -> no download, no PE signal.
    gh = FakeGitHub(
        tree=["README.md"],
        releases=[release(("App-x64.7z", "application/x-7z", 80000000))],
    )
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    assert pe is None
    assert not gh.downloaded
    pe_sig = next(s for s in sigs if s.key == "pe_metadata_mismatch")
    assert pe_sig.triggered is False


def test_evaluate_handles_download_error():
    gh = FakeGitHub(
        tree=["README.md"],
        releases=[release(("App.exe", "x", 10))],
        download_error=GitHubError("too big"),
    )
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    assert pe is None  # download failed -> no PE metadata, no crash


def test_evaluate_handles_release_error():
    gh = FakeGitHub(tree=["README.md"], release_error=GitHubError("boom"))
    cand, sigs, pe = signals.evaluate(make_candidate(), make_project(), make_config(), gh)
    assert cand.has_releases is False


def test_evaluate_metadata_only_skips_pe_download():
    # read_pe=False: no binary is downloaded or parsed, PE signal cannot fire.
    gh = FakeGitHub(
        tree=["README.md", "lib"],
        releases=[release(("App-Setup.exe", "x", 1536))],
        asset_bytes=(FIXTURES / "pe_mismatch.exe").read_bytes(),
    )
    cand, sigs, pe = signals.evaluate(
        make_candidate(), make_project(), make_config(), gh, read_pe=False
    )
    assert pe is None
    assert gh.downloaded == []  # binary never fetched
    pe_sig = next(s for s in sigs if s.key == "pe_metadata_mismatch")
    assert pe_sig.triggered is False
