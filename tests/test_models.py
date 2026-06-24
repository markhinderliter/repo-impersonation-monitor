"""Smoke tests for the data model (mostly trivial properties + enum parsing)."""

from datetime import datetime

import pytest

from repo_impersonation_monitor.models import ConfidenceTier, Project


def test_project_full_name():
    p = Project(
        owner="acme", name="Widget",
        html_url="https://github.com/acme/Widget",
        created_at=datetime(2025, 1, 1), has_releases=False,
    )
    assert p.full_name == "acme/Widget"


def test_tier_ordering():
    assert ConfidenceTier.HIGH > ConfidenceTier.MEDIUM > ConfidenceTier.LOW > ConfidenceTier.IGNORE


def test_tier_from_name_roundtrip():
    assert ConfidenceTier.from_name("high") is ConfidenceTier.HIGH


def test_tier_from_name_invalid():
    with pytest.raises(ValueError):
        ConfidenceTier.from_name("nope")
