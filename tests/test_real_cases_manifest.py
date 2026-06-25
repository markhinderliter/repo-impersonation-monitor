"""Schema/consistency tests for the real-cases ground-truth manifest.

Pure local: reads the YAML file only — never contacts GitHub.
"""

import re
from pathlib import Path

import yaml

MANIFEST = Path(__file__).parent / "fixtures" / "real_cases.yaml"
ALLOWED_LABELS = {"do_not_flag", "candidate_to_classify"}
REQUIRED_KEYS = {"canonical_repo", "expected_label", "ground_truth_evidence"}
REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")
# Popularity is explicitly not ground truth — these keys must never appear.
POPULARITY_KEYS = {"stars", "stargazers", "star_count", "popularity", "forks", "watchers"}


def load_cases():
    data = yaml.safe_load(MANIFEST.read_text())
    return data["cases"]


def test_manifest_parses_with_cases():
    cases = load_cases()
    assert isinstance(cases, list) and cases


def test_each_case_has_required_schema():
    for case in load_cases():
        assert REQUIRED_KEYS <= set(case), f"missing keys in {case}"
        assert REPO_RE.match(case["canonical_repo"]), case["canonical_repo"]
        assert case["expected_label"] in ALLOWED_LABELS, case["expected_label"]
        evidence = case["ground_truth_evidence"]
        assert isinstance(evidence, str) and evidence.strip(), case["canonical_repo"]


def test_no_popularity_fields_anywhere():
    for case in load_cases():
        leaked = set(case) & POPULARITY_KEYS
        assert not leaked, f"popularity is not ground truth: {leaked} in {case['canonical_repo']}"


def test_canonical_repos_are_unique():
    repos = [c["canonical_repo"] for c in load_cases()]
    assert len(repos) == len(set(repos))


def test_known_cases_present():
    repos = {c["canonical_repo"] for c in load_cases()}
    assert "bytedance/deer-flow" in repos
    assert "bigdatasciencegroup/bytedance-deer-flow" in repos


def test_unverified_lookalike_is_candidate_not_confirmed():
    by_repo = {c["canonical_repo"]: c for c in load_cases()}
    case = by_repo["bigdatasciencegroup/bytedance-deer-flow"]
    assert case["expected_label"] == "candidate_to_classify"
    # must be explicitly marked unverified, never asserted as confirmed-malicious
    assert "unverified" in case["ground_truth_evidence"].lower()
