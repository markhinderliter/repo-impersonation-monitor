"""Tests for the paste-ready abuse report renderer.

The report is the project's actual deliverable. It must read as factual,
evidence-led observation — never a guess — and must always carry the
never-executed statement and an allowlist caveat.
"""

from datetime import datetime

import pytest

from repo_impersonation_monitor import scoring
from repo_impersonation_monitor.config import load_config
from repo_impersonation_monitor.models import (
    Candidate,
    ConfidenceTier,
    PeMetadata,
    Project,
    ScoredCandidate,
    SignalResult,
)
from repo_impersonation_monitor.report import dedupe_marker, render


def make_config():
    return load_config(
        {
            "INPUT_PROJECT_NAME": "MyProject",
            "INPUT_PROJECT_REPO": "realowner/MyProject",
            "INPUT_GITHUB_TOKEN": "tok",
        }
    )


def make_project():
    return Project(
        owner="realowner",
        name="MyProject",
        html_url="https://github.com/realowner/MyProject",
        created_at=datetime(2025, 1, 1),
        has_releases=False,
        source_dir_markers=("src", "tests"),
        description="The real project",
    )


def make_candidate():
    ts = datetime(2026, 6, 22, 10, 42)
    return Candidate(
        owner="evil",
        name="MyProject",
        html_url="https://github.com/evil/MyProject",
        clone_url="https://github.com/evil/MyProject.git",
        is_fork=False,
        created_at=ts,
        pushed_at=ts,
        discovered_via="bare-org",
        description="The real project",
    )


def sig(key, detail):
    return SignalResult(
        key=key, label=key.replace("_", " "), triggered=True,
        weight=scoring.SIGNAL_WEIGHTS[key], detail=detail,
    )


def make_scored(signals=None, pe=None, tier=ConfidenceTier.HIGH):
    signals = signals or (
        sig("source_tree_stripped", "Top-level tree has no src/ or tests/ dir."),
        sig("not_a_fork", "Repo is not a fork of the real project."),
        sig("pe_metadata_mismatch", "ProductName 'Janus Key' is unrelated."),
    )
    return ScoredCandidate(
        candidate=make_candidate(), signals=signals, score=0.85, tier=tier, pe_metadata=pe
    )


def render_default(**kw):
    return render(make_scored(**kw), make_project(), make_config())


def test_report_has_title_and_body():
    r = render_default()
    assert r.title
    assert r.body


def test_title_names_the_candidate():
    r = render_default()
    assert "evil/MyProject" in r.title


def test_body_contains_real_and_candidate_urls():
    r = render_default()
    assert "https://github.com/realowner/MyProject" in r.body
    assert "https://github.com/evil/MyProject" in r.body


def test_body_lists_triggered_signal_details():
    r = render_default()
    assert "no src/ or tests/ dir" in r.body
    assert "not a fork" in r.body


def test_untriggered_signals_excluded_from_body():
    signals = (
        sig("source_tree_stripped", "stripped detail here"),
        SignalResult(
            key="ships_binary_real_does_not", label="ships binary",
            triggered=False, weight=0.2, detail="SHOULD NOT APPEAR",
        ),
    )
    r = render(make_scored(signals=signals), make_project(), make_config())
    assert "SHOULD NOT APPEAR" not in r.body


def test_never_executed_statement_always_present():
    # Even with no PE metadata, the guarantee statement must appear.
    r = render_default()
    assert "never executed" in r.body.lower()


def test_pe_fields_rendered_when_present():
    pe = PeMetadata(
        product_name="Janus Key",
        company_name="Duality Solutions",
        original_filename="janus.exe",
        parse_ok=True,
    )
    r = render_default(pe=pe)
    assert "Janus Key" in r.body
    assert "Duality Solutions" in r.body


def test_pe_section_handles_missing_metadata():
    r = render_default(pe=None)
    # No crash, and no stray "None" leaking as a product name line.
    assert "ProductName: None" not in r.body


def test_abuse_categories_suggested():
    r = render_default()
    body = r.body.lower()
    assert "trademark" in body or "copyright" in body or "impersonation" in body


def test_allowlist_caveat_present():
    r = render_default()
    assert "allowlist" in r.body.lower()


def test_confidence_tier_shown():
    r = render_default()
    assert "HIGH" in r.body


def test_dedupe_marker_stable_and_lowercased():
    marker = dedupe_marker(make_candidate())
    assert marker == "<!-- rim:evil/myproject -->"


def test_body_embeds_dedupe_marker():
    r = render_default()
    assert dedupe_marker(make_candidate()) in r.body


def test_discovered_via_provenance_shown():
    r = render_default()
    assert "bare-org" in r.body


@pytest.mark.parametrize("tier", [ConfidenceTier.MEDIUM, ConfidenceTier.LOW])
def test_renders_for_lower_tiers_without_error(tier):
    r = render_default(tier=tier)
    assert r.body
    assert tier.name in r.body


def test_observations_fallback_when_none_triggered():
    untriggered = SignalResult(
        key="not_a_fork", label="not a fork", triggered=False, weight=0.15,
    )
    scored = ScoredCandidate(
        candidate=make_candidate(), signals=(untriggered,),
        score=0.0, tier=ConfidenceTier.IGNORE,
    )
    r = render(scored, make_project(), make_config())
    assert "no individual signals triggered" in r.body


def test_pe_section_omitted_when_all_fields_empty():
    pe = PeMetadata(parse_ok=True)  # parsed OK but no usable fields
    r = render_default(pe=pe)
    assert "version-resource fields" not in r.body
