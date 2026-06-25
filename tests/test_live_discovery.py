"""Offline unit tests for the live discovery runner's pure helpers.

No network: every test builds synthetic ScoredCandidate objects and exercises the
aggregation / formatting / truncation / look-alike logic directly. The live
orchestration (generate() over the network) is deliberately not exercised here.
"""

from __future__ import annotations

from datetime import datetime

import live_discovery as ld

from repo_impersonation_monitor.models import (
    Candidate,
    ConfidenceTier,
    ScoredCandidate,
    SignalResult,
)

_DT = datetime(2026, 1, 1)


def make_scored(
    full_name: str,
    tier: ConfidenceTier,
    *,
    via: str = "exact-name",
    fired: tuple[str, ...] = (),
) -> ScoredCandidate:
    owner, name = full_name.split("/", 1)
    candidate = Candidate(
        owner=owner,
        name=name,
        html_url=f"https://github.com/{full_name}",
        clone_url=f"https://github.com/{full_name}.git",
        is_fork=False,
        created_at=_DT,
        pushed_at=_DT,
        discovered_via=via,
    )
    sigs = tuple(
        SignalResult(key=key, label=key, triggered=True, weight=0.1) for key in fired
    )
    return ScoredCandidate(candidate=candidate, signals=sigs, score=0.0, tier=tier)


def test_row_for_formats_repo_via_tier_signals():
    sc = make_scored(
        "evil/skills",
        ConfidenceTier.MEDIUM,
        via="exact-name",
        fired=("owner_mismatch", "not_a_fork"),
    )
    assert ld.row_for(sc) == (
        "evil/skills",
        "exact-name",
        "MEDIUM",
        "owner_mismatch, not_a_fork",
    )


def test_row_for_no_signals_uses_dash():
    sc = make_scored("x/skills", ConfidenceTier.IGNORE)
    assert ld.row_for(sc)[3] == "-"


def test_tier_counts_zero_fills_all_tiers():
    scored = [
        make_scored("a/skills", ConfidenceTier.IGNORE),
        make_scored("b/skills", ConfidenceTier.LOW),
        make_scored("c/skills", ConfidenceTier.LOW),
        make_scored("d/skills", ConfidenceTier.MEDIUM),
    ]
    assert ld.tier_counts(scored) == {"IGNORE": 1, "LOW": 2, "MEDIUM": 1, "HIGH": 0}


def test_tier_counts_empty_still_lists_every_tier():
    assert ld.tier_counts([]) == {"IGNORE": 0, "LOW": 0, "MEDIUM": 0, "HIGH": 0}


def test_is_truncated_at_and_above_cap():
    assert ld.is_truncated(30, 30) is True
    assert ld.is_truncated(31, 30) is True


def test_is_truncated_below_cap():
    assert ld.is_truncated(29, 30) is False


def test_elevated_returns_medium_and_high_only():
    scored = [
        make_scored("a/x", ConfidenceTier.IGNORE),
        make_scored("b/x", ConfidenceTier.LOW),
        make_scored("c/x", ConfidenceTier.MEDIUM),
        make_scored("d/x", ConfidenceTier.HIGH),
    ]
    names = {sc.candidate.full_name for sc in ld.elevated(scored)}
    assert names == {"c/x", "d/x"}


def test_lookalike_surfaced_true_and_false():
    scored = [make_scored("bigdatasciencegroup/bytedance-deer-flow", ConfidenceTier.LOW)]
    assert ld.lookalike_surfaced(scored, "bigdatasciencegroup/bytedance-deer-flow") is True
    assert ld.lookalike_surfaced(scored, "other/deer-flow") is False


def test_render_seed_section_truncation_note_present_at_cap():
    scored = [make_scored(f"o{i}/skills", ConfidenceTier.LOW) for i in range(30)]
    out = ld.render_seed_section("mattpocock/skills", scored, 30)
    assert "Truncated at cap" in out
    assert "Total surfaced:** 30" in out


def test_render_seed_section_no_truncation_below_cap():
    scored = [make_scored("a/skills", ConfidenceTier.LOW)]
    out = ld.render_seed_section("mattpocock/skills", scored, 30)
    assert "Truncated at cap" not in out


def test_render_seed_section_deer_flow_lookalike_excluded_callout():
    # deer-flow seed, look-alike NOT in the surfaced set -> the fork-exclusion finding
    # (it's a fork of the real project, invisible to search regardless of name).
    scored = [make_scored("someone/deer-flow", ConfidenceTier.LOW)]
    out = ld.render_seed_section("bytedance/deer-flow", scored, 30)
    assert "was NOT surfaced" in out
    assert "it is a fork of the real project" in out
    assert "weaponized-fork gap (THREAT_MODEL §6)" in out


def test_render_seed_section_noise_floor_clean_when_all_low():
    scored = [
        make_scored("a/gstack", ConfidenceTier.IGNORE),
        make_scored("b/gstack", ConfidenceTier.LOW),
    ]
    out = ld.render_seed_section("garrytan/gstack", scored, 30)
    assert "tiered the name-twins down correctly" in out


def test_render_seed_section_flags_elevated_repos():
    scored = [
        make_scored(
            "evil/gstack",
            ConfidenceTier.MEDIUM,
            fired=("owner_mismatch", "ships_binary_real_does_not"),
        )
    ]
    out = ld.render_seed_section("garrytan/gstack", scored, 30)
    assert "Noise floor" in out
    assert "evil/gstack" in out


def test_render_seed_section_handles_empty_surface():
    out = ld.render_seed_section("garrytan/gstack", [], 30)
    assert "Total surfaced:** 0" in out
    assert "_(none surfaced)_" in out


def test_seeds_and_watch_target_are_consistent():
    # The deer-flow watch target's seed must be one of the configured seeds.
    for seed in ld.WATCH_LOOKALIKE:
        assert seed in ld.SEEDS
