"""Combine signal results into a score and a conservative confidence tier.

Two-part gate (serves "propose, never auto-accuse"):

1. A weighted ``score`` = sum of triggered signal weights (capped at 1.0).
2. A structural requirement: HIGH needs >= 3 strong signals AND at least one
   *core* signal (PE-resource mismatch or stripped source tree).

Because the largest single weight (0.40) is below ``HIGH_SCORE`` and the
strong-count gate requires corroboration, no single signal can ever reach HIGH.

This module is the single source of truth for signal weights and their
classification; ``signals`` imports these constants so a SignalResult's weight
matches the table here.
"""

from __future__ import annotations

from .models import Candidate, ConfidenceTier, PeMetadata, ScoredCandidate, SignalResult

# Canonical weight per signal key. Structural signals are weighted high; the
# decaying behavioral signals are weighted low (attackers adapt these away).
SIGNAL_WEIGHTS: dict[str, float] = {
    # structural / strong
    "pe_metadata_mismatch": 0.40,
    "source_tree_stripped": 0.30,
    "ships_binary_real_does_not": 0.20,
    "platform_path_inconsistency": 0.15,
    "not_a_fork": 0.15,
    "owner_mismatch": 0.10,
    "created_after_real": 0.10,
    # behavioral / weak / decaying
    "readme_only_commit_churn": 0.05,
    "hourly_readme_recommit": 0.05,
}

# Strong = durable structural signal. Behavioral signals are deliberately excluded
# so they can never satisfy the strong-count gate on their own.
STRONG_KEYS: frozenset[str] = frozenset(
    {
        "pe_metadata_mismatch",
        "source_tree_stripped",
        "ships_binary_real_does_not",
        "platform_path_inconsistency",
        "not_a_fork",
        "owner_mismatch",
        "created_after_real",
    }
)

# Core = the "looks like the project but isn't underneath" tells. At least one is
# required for a HIGH tier — prevents a legit-but-renamed copy being flagged HIGH
# on weak corroboration alone.
CORE_KEYS: frozenset[str] = frozenset({"pe_metadata_mismatch", "source_tree_stripped"})

# Thresholds.
HIGH_SCORE = 0.60
HIGH_STRONG = 3
MEDIUM_SCORE = 0.40
MEDIUM_STRONG = 2
LOW_SCORE = 0.20


def tier_for(score: float, strong_triggered: int, core_triggered: bool) -> ConfidenceTier:
    """Map a score + corroboration counts to a confidence tier."""
    if score >= HIGH_SCORE and strong_triggered >= HIGH_STRONG and core_triggered:
        return ConfidenceTier.HIGH
    if score >= MEDIUM_SCORE and strong_triggered >= MEDIUM_STRONG:
        return ConfidenceTier.MEDIUM
    if score >= LOW_SCORE:
        return ConfidenceTier.LOW
    return ConfidenceTier.IGNORE


def score(
    candidate: Candidate,
    signals: tuple[SignalResult, ...],
    pe_metadata: PeMetadata | None,
) -> ScoredCandidate:
    """Score a candidate and assign a confidence tier."""
    triggered = [s for s in signals if s.triggered]
    raw_score = sum(s.weight for s in triggered)
    total = min(raw_score, 1.0)

    strong_triggered = sum(1 for s in triggered if s.key in STRONG_KEYS)
    core_triggered = any(s.key in CORE_KEYS for s in triggered)

    tier = tier_for(total, strong_triggered, core_triggered)

    return ScoredCandidate(
        candidate=candidate,
        signals=tuple(signals),
        score=total,
        tier=tier,
        pe_metadata=pe_metadata,
    )
