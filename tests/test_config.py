"""Tests for config parsing/validation (system boundary — validate hard)."""

import pytest

from repo_impersonation_monitor.config import ConfigError, load_config
from repo_impersonation_monitor.models import ConfidenceTier


def base_env(**overrides):
    env = {
        "INPUT_PROJECT_NAME": "MyProject",
        "INPUT_PROJECT_REPO": "realowner/MyProject",
        "INPUT_GITHUB_TOKEN": "tok_123",
    }
    env.update(overrides)
    return env


def test_minimal_valid_config():
    cfg = load_config(base_env())
    assert cfg.project_name == "MyProject"
    assert cfg.project_repo == "realowner/MyProject"
    assert cfg.project_owner == "realowner"
    assert cfg.github_token == "tok_123"


def test_real_url_defaults_from_repo():
    cfg = load_config(base_env())
    assert cfg.real_html_url == "https://github.com/realowner/MyProject"


def test_real_url_override_respected():
    cfg = load_config(base_env(INPUT_REAL_URL="https://example.com/x"))
    assert cfg.real_html_url == "https://example.com/x"


def test_report_repo_defaults_to_project_repo():
    cfg = load_config(base_env())
    assert cfg.report_repo == "realowner/MyProject"


def test_report_repo_override():
    cfg = load_config(base_env(INPUT_REPORT_REPO="realowner/reports"))
    assert cfg.report_repo == "realowner/reports"


def test_min_tier_defaults_to_high():
    cfg = load_config(base_env())
    assert cfg.min_tier_to_report is ConfidenceTier.HIGH


def test_min_tier_parsed_case_insensitive():
    cfg = load_config(base_env(INPUT_MIN_TIER="medium"))
    assert cfg.min_tier_to_report is ConfidenceTier.MEDIUM


def test_invalid_min_tier_raises():
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_MIN_TIER="bogus"))


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("realowner/dup, evil/MyProject", {"realowner/dup", "evil/myproject"}),
        ("a/b\nC/D\n\n  e/f  ", {"a/b", "c/d", "e/f"}),
        ("", set()),
    ],
)
def test_allowlist_normalized_lowercased_and_split(raw, expected):
    cfg = load_config(base_env(INPUT_ALLOWLIST=raw))
    # allowlist is lowercased for case-insensitive matching
    assert set(cfg.allowlist) == {e.lower() for e in expected}


def test_allowlist_is_frozenset():
    cfg = load_config(base_env(INPUT_ALLOWLIST="a/b"))
    assert isinstance(cfg.allowlist, frozenset)


@pytest.mark.parametrize("flag,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True),
    ("false", False), ("False", False), ("0", False), ("", False), ("no", False),
])
def test_dry_run_parsing(flag, expected):
    cfg = load_config(base_env(INPUT_DRY_RUN=flag))
    assert cfg.dry_run is expected


def test_max_candidates_default_and_override():
    assert load_config(base_env()).max_candidates > 0
    assert load_config(base_env(INPUT_MAX_CANDIDATES="25")).max_candidates == 25


def test_max_candidates_invalid_raises():
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_MAX_CANDIDATES="-5"))
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_MAX_CANDIDATES="notanint"))


def test_max_variants_default_and_override():
    assert load_config(base_env()).max_variants == 20
    assert load_config(base_env(INPUT_MAX_VARIANTS="8")).max_variants == 8


def test_max_variants_invalid_raises():
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_MAX_VARIANTS="0"))
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_MAX_VARIANTS="notanint"))


def test_missing_project_name_raises():
    env = base_env()
    del env["INPUT_PROJECT_NAME"]
    with pytest.raises(ConfigError):
        load_config(env)


@pytest.mark.parametrize("bad", ["noslash", "too/many/parts", "/leading", "trailing/", ""])
def test_invalid_project_repo_raises(bad):
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_PROJECT_REPO=bad))


def test_token_required_unless_dry_run():
    env = base_env()
    del env["INPUT_GITHUB_TOKEN"]
    with pytest.raises(ConfigError):
        load_config(env)


def test_token_optional_in_dry_run():
    env = base_env(INPUT_DRY_RUN="true")
    del env["INPUT_GITHUB_TOKEN"]
    cfg = load_config(env)
    assert cfg.dry_run is True
    assert cfg.github_token == ""


def test_invalid_report_repo_raises():
    with pytest.raises(ConfigError):
        load_config(base_env(INPUT_REPORT_REPO="not-a-repo"))


def test_project_owner_derived_from_repo():
    cfg = load_config(base_env(INPUT_PROJECT_REPO="acme/Widget"))
    assert cfg.project_owner == "acme"
