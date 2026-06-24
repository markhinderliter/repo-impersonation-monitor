"""Tests for the scoring model and the conservative confidence-tier gate.

The central safety property: no single signal can reach HIGH, and HIGH always
requires a 'core' structural signal (PE mismatch or stripped source) plus
corroboration. These tests lock that in.
"""

from datetime import datetime

import pytest

from repo_impersonation_monitor import scoring
from repo_impersonation_monitor.models import (
    Candidate,
    ConfidenceTier,
    SignalResult,
)


def make_candidate() -> Candidate:
    ts = datetime(2026, 6, 22)
    return Candidate(
        owner="evil",
        name="MyProject",
        html_url="https://github.com/evil/MyProject",
        clone_url="https://github.com/evil/MyProject.git",
        is_fork=False,
        created_at=ts,
        pushed_at=ts,
        discovered_via="exact-name",
    )


def sig(key: str, triggered: bool = True) -> SignalResult:
    """Build a SignalResult using the module's canonical weight for ``key``."""
    return SignalResult(
        key=key,
        label=key,
        triggered=triggered,
        weight=scoring.SIGNAL_WEIGHTS[key],
    )


def score_keys(*keys: str):
    return scoring.score(make_candidate(), tuple(sig(k) for k in keys), None)


def test_no_signals_is_ignore():
    result = score_keys()
    assert result.score == 0.0
    assert result.tier is ConfidenceTier.IGNORE


def test_only_triggered_signals_count():
    untriggered = sig("source_tree_stripped", triggered=False)
    result = scoring.score(make_candidate(), (untriggered,), None)
    assert result.score == 0.0
    assert result.tier is ConfidenceTier.IGNORE


def test_score_sums_triggered_weights():
    result = score_keys("source_tree_stripped", "ships_binary_real_does_not")
    expected = (
        scoring.SIGNAL_WEIGHTS["source_tree_stripped"]
        + scoring.SIGNAL_WEIGHTS["ships_binary_real_does_not"]
    )
    assert result.score == pytest.approx(expected)


def test_score_capped_at_one():
    result = score_keys(*scoring.SIGNAL_WEIGHTS.keys())  # trigger everything
    assert result.score <= 1.0


def test_high_requires_core_plus_three_strong():
    # pe(core) + stripped(core) + ships = 0.90, 3 strong, core present -> HIGH
    result = score_keys(
        "pe_metadata_mismatch", "source_tree_stripped", "ships_binary_real_does_not"
    )
    assert result.tier is ConfidenceTier.HIGH


def test_high_blocked_without_core_signal():
    # Strong, high-scoring, but NO pe/stripped core signal -> capped at MEDIUM.
    result = score_keys(
        "ships_binary_real_does_not",
        "not_a_fork",
        "owner_mismatch",
        "created_after_real",
        "platform_path_inconsistency",
    )
    assert result.score >= scoring.HIGH_SCORE  # score alone would qualify
    assert result.tier is ConfidenceTier.MEDIUM  # but the core gate blocks HIGH


def test_no_single_signal_reaches_high():
    for key in scoring.SIGNAL_WEIGHTS:
        result = score_keys(key)
        assert result.tier < ConfidenceTier.HIGH, f"{key} alone reached HIGH"


def test_medium_boundary_core_plus_two_strong():
    # pe(0.40) + owner(0.10) = 0.50, 2 strong, core present -> MEDIUM
    result = score_keys("pe_metadata_mismatch", "owner_mismatch")
    assert result.tier is ConfidenceTier.MEDIUM


def test_low_when_score_below_medium_threshold():
    # owner(0.10) + created(0.10) = 0.20, 2 strong, but score < MEDIUM_SCORE -> LOW
    result = score_keys("owner_mismatch", "created_after_real")
    assert result.score == pytest.approx(0.20)
    assert result.tier is ConfidenceTier.LOW


def test_single_strong_signal_is_low():
    result = score_keys("source_tree_stripped")  # 0.30, 1 strong
    assert result.tier is ConfidenceTier.LOW


def test_behavioral_only_is_ignore():
    # behavioral signals are weak and not 'strong' — cannot satisfy the gate
    result = score_keys("readme_only_commit_churn", "hourly_readme_recommit")
    assert result.tier is ConfidenceTier.IGNORE


def test_behavioral_signals_are_not_strong():
    assert "readme_only_commit_churn" not in scoring.STRONG_KEYS
    assert "hourly_readme_recommit" not in scoring.STRONG_KEYS


def test_core_keys_are_strong():
    assert scoring.CORE_KEYS <= scoring.STRONG_KEYS


def test_scored_candidate_preserves_signals_and_pe():
    sigs = (sig("pe_metadata_mismatch"),)
    result = scoring.score(make_candidate(), sigs, None)
    assert result.signals == sigs
    assert result.candidate.full_name == "evil/MyProject"
