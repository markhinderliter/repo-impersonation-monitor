"""Detection-accuracy tests against seeded fixtures with known correct answers.

Runs the real pipeline (signals.evaluate -> scoring.score) against synthetic repo
snapshots served from temp dirs by LocalSnapshotClient. No network; synthesized
binaries are parsed, never executed.
"""

from repo_impersonation_monitor import scoring, signals
from repo_impersonation_monitor.config import load_config
from repo_impersonation_monitor.models import ConfidenceTier
from tests.fixtures.seeded.builder import build_case

CONFIG = load_config(
    {
        "INPUT_PROJECT_NAME": "acme-cli",
        "INPUT_PROJECT_REPO": "acme/acme-cli",
        "INPUT_GITHUB_TOKEN": "x",
    }
)

CORE_AND_BINARY = {"source_tree_stripped", "pe_metadata_mismatch", "ships_binary_real_does_not"}


def run_case(tmp_path, case):
    project, candidate, client = build_case(tmp_path, case)
    enriched, sig_results, pe = signals.evaluate(candidate, project, CONFIG, client)
    scored = scoring.score(enriched, tuple(sig_results), pe)
    triggered = {s.key for s in scored.triggered_signals}
    return scored, triggered


def test_evil_twin_is_high_confidence(tmp_path):
    scored, triggered = run_case(tmp_path, "evil_twin")
    assert scored.tier is ConfidenceTier.HIGH
    # the high-confidence verdict rests on the core structural tells, not noise
    assert CORE_AND_BINARY <= triggered


def test_legit_fork_is_not_flagged(tmp_path):
    scored, triggered = run_case(tmp_path, "legit_fork")
    # tightened to the tier we actually expect, not merely < HIGH
    assert scored.tier <= ConfidenceTier.LOW
    # the key legitimacy signal: it IS a fork, so not_a_fork must not fire
    assert "not_a_fork" not in triggered
    assert not (CORE_AND_BINARY & triggered)


def test_benign_mirror_does_not_false_positive(tmp_path):
    scored, triggered = run_case(tmp_path, "benign_mirror")
    # the hard near-miss must never reach MEDIUM/HIGH
    assert scored.tier < ConfidenceTier.MEDIUM
    # it shares the scary surface with the evil twin...
    assert {"owner_mismatch", "not_a_fork", "created_after_real"} <= triggered
    # ...and is separated from it ONLY by the absent core structural tells
    assert not (CORE_AND_BINARY & triggered)


def test_evil_twin_and_benign_mirror_share_surface_signals(tmp_path):
    """The near-miss is meaningful only if it overlaps the twin on surface tells."""
    _, evil = run_case(tmp_path, "evil_twin")
    _, benign = run_case(tmp_path, "benign_mirror")
    shared = evil & benign
    assert {"owner_mismatch", "not_a_fork", "created_after_real"} <= shared
    # the difference is exactly the core/binary tells
    assert CORE_AND_BINARY <= (evil - benign)
