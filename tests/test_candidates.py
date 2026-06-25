"""Tests for candidate generation (exact-name + bare-org)."""

from datetime import UTC, datetime

import pytest

from repo_impersonation_monitor.candidates import (
    cap,
    exclude_self_and_allowlist,
    generate,
    item_to_candidate,
    name_variants,
)
from repo_impersonation_monitor.config import load_config
from repo_impersonation_monitor.models import Candidate


def make_config(**overrides):
    env = {
        "INPUT_PROJECT_NAME": "MyProject",
        "INPUT_PROJECT_REPO": "realowner/MyProject",
        "INPUT_GITHUB_TOKEN": "tok",
    }
    env.update(overrides)
    return load_config(env)


def search_item(owner, name, *, fork=False, created="2026-06-22T10:42:32Z", desc="d"):
    full = f"{owner}/{name}"
    return {
        "name": name,
        "full_name": full,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full}",
        "clone_url": f"https://github.com/{full}.git",
        "fork": fork,
        "created_at": created,
        "pushed_at": created,
        "description": desc,
    }


class FakeGitHub:
    def __init__(self, items):
        self._items = items
        self.queries = []

    def search_repos(self, query, *, per_page=100, max_pages=10, sort=None):
        self.queries.append(
            {"query": query, "per_page": per_page, "max_pages": max_pages, "sort": sort}
        )
        return list(self._items)


class QueryMapGitHub:
    """Returns items keyed by exact query string; records every query + its sort."""

    def __init__(self, by_query):
        self._by_query = by_query
        self.queries = []

    def search_repos(self, query, *, per_page=100, max_pages=10, sort=None):
        self.queries.append({"query": query, "sort": sort, "max_pages": max_pages})
        return list(self._by_query.get(query, []))


# --- item_to_candidate ----------------------------------------------------

def test_item_to_candidate_maps_fields():
    c = item_to_candidate(search_item("evil", "MyProject"), "exact-name")
    assert isinstance(c, Candidate)
    assert c.owner == "evil"
    assert c.name == "MyProject"
    assert c.full_name == "evil/MyProject"
    assert c.clone_url == "https://github.com/evil/MyProject.git"
    assert c.is_fork is False
    assert c.discovered_via == "exact-name"
    assert c.description == "d"


def test_item_to_candidate_parses_iso_dates():
    c = item_to_candidate(search_item("evil", "MyProject"), "exact-name")
    assert c.created_at == datetime(2026, 6, 22, 10, 42, 32, tzinfo=UTC)


def test_item_to_candidate_handles_missing_description():
    item = search_item("evil", "MyProject", desc=None)
    item.pop("description")
    c = item_to_candidate(item, "exact-name")
    assert c.description is None


# --- generate: exact-name + bare-org -------------------------------------

def test_generate_keeps_exact_name_matches_only():
    items = [
        search_item("evil", "MyProject"),       # exact
        search_item("someone", "MyProjectUI"),  # fuzzy, different name
        search_item("other", "my-project"),     # different name
    ]
    gh = FakeGitHub(items)
    out = generate(make_config(), gh)
    names = {c.full_name for c in out}
    assert names == {"evil/MyProject"}


def test_generate_is_case_insensitive_on_name():
    gh = FakeGitHub([search_item("evil", "myproject")])
    out = generate(make_config(), gh)
    assert [c.full_name for c in out] == ["evil/myproject"]


def test_generate_labels_bare_org_provenance():
    gh = FakeGitHub([
        search_item("evil", "MyProject"),          # exact-name
        search_item("MyProject", "MyProject"),     # bare-org (owner==repo==name)
    ])
    out = {c.full_name: c.discovered_via for c in generate(make_config(), gh)}
    assert out["evil/MyProject"] == "exact-name"
    assert out["MyProject/MyProject"] == "bare-org"


def test_generate_includes_forks_for_scoring():
    # Forks are NOT filtered out — they are scored low later via not_a_fork.
    gh = FakeGitHub([search_item("forker", "MyProject", fork=True)])
    out = generate(make_config(), gh)
    assert len(out) == 1
    assert out[0].is_fork is True


def test_generate_query_targets_name():
    gh = FakeGitHub([])
    generate(make_config(), gh)
    assert "MyProject" in gh.queries[0]["query"]
    assert "in:name" in gh.queries[0]["query"]


# --- exclusion: self + allowlist -----------------------------------------

def test_generate_excludes_self():
    gh = FakeGitHub([
        search_item("realowner", "MyProject"),  # the real repo itself
        search_item("evil", "MyProject"),
    ])
    out = {c.full_name for c in generate(make_config(), gh)}
    assert out == {"evil/MyProject"}


def test_generate_excludes_allowlist_case_insensitive():
    gh = FakeGitHub([
        search_item("Mirror", "MyProject"),
        search_item("evil", "MyProject"),
    ])
    cfg = make_config(INPUT_ALLOWLIST="mirror/myproject")
    out = {c.full_name for c in generate(cfg, gh)}
    assert out == {"evil/MyProject"}


def test_exclude_self_and_allowlist_unit():
    cands = [
        item_to_candidate(search_item("realowner", "MyProject"), "exact-name"),
        item_to_candidate(search_item("ok", "MyProject"), "exact-name"),
        item_to_candidate(search_item("good", "MyProject"), "exact-name"),
    ]
    cfg = make_config(INPUT_ALLOWLIST="good/MyProject")
    out = {c.full_name for c in exclude_self_and_allowlist(cands, cfg)}
    assert out == {"ok/MyProject"}


# --- dedupe + cap ---------------------------------------------------------

def test_generate_dedupes_by_full_name():
    item = search_item("evil", "MyProject")
    gh = FakeGitHub([item, dict(item)])  # same repo twice
    out = generate(make_config(), gh)
    assert len(out) == 1


def test_cap_limits_results():
    cands = [
        item_to_candidate(search_item(f"o{i}", "MyProject"), "exact-name")
        for i in range(10)
    ]
    assert len(cap(cands, 3)) == 3


def test_generate_respects_max_candidates():
    items = [search_item(f"evil{i}", "MyProject") for i in range(20)]
    gh = FakeGitHub(items)
    out = generate(make_config(INPUT_MAX_CANDIDATES="5"), gh)
    assert len(out) == 5


@pytest.mark.parametrize("max_c,expected_pages", [(50, 1), (150, 2), (5000, 10)])
def test_max_pages_bounded_by_max_candidates(max_c, expected_pages):
    gh = FakeGitHub([])
    generate(make_config(INPUT_MAX_CANDIDATES=str(max_c)), gh)
    assert gh.queries[0]["max_pages"] == expected_pages


# --- permutation: variant generation (pure) ------------------------------

def test_name_variants_includes_org_fold():
    # Org-fold generation: owner 'bytedance' + name 'deer-flow' -> 'bytedance-deer-flow'.
    # (Synthetic check of the generator; the real repo of that name is a fork, so it
    # is excluded from search by default — a separate gap, see THREAT_MODEL §6.)
    variants = dict(name_variants("bytedance", "deer-flow"))
    assert variants["bytedance-deer-flow"] == "permutation:org-fold"


def test_name_variants_includes_separator_swaps():
    names = {v for v, _ in name_variants("o", "deer-flow")}
    assert {"deerflow", "deer_flow", "deer.flow"} <= names


def test_name_variants_includes_prefix_and_suffix_affixes():
    names = {v for v, _ in name_variants("o", "deer-flow")}
    assert "deer-flow-ai" in names
    assert "ai-deer-flow" in names


def test_name_variants_excludes_the_exact_name():
    names = {v for v, _ in name_variants("o", "deer-flow")}
    assert "deer-flow" not in names


def test_name_variants_are_deduped():
    names = [v for v, _ in name_variants("bytedance", "deer-flow")]
    assert len(names) == len(set(names))


def test_name_variants_single_token_has_no_separator_swaps():
    vias = {via for _, via in name_variants("acme", "skills")}
    assert "permutation:separator" not in vias  # nothing to swap on a 1-token name
    assert "permutation:org-fold" in vias
    assert "permutation:affix" in vias


def test_name_variants_org_fold_precedes_affixes_for_cap_truncation():
    kinds = [via for _, via in name_variants("bytedance", "deer-flow")]
    assert kinds[0] == "permutation:org-fold"
    assert kinds.index("permutation:org-fold") < kinds.index("permutation:affix")


# --- permutation: generate() integration ---------------------------------

def perm_config(**overrides):
    env = {"INPUT_PROJECT_NAME": "deer-flow", "INPUT_PROJECT_REPO": "bytedance/deer-flow"}
    env.update(overrides)
    return make_config(**env)


def test_generate_surfaces_org_fold_via_permutation():
    fold = search_item("bigdatasciencegroup", "bytedance-deer-flow")
    gh = QueryMapGitHub({"bytedance-deer-flow in:name": [fold]})
    out = {c.full_name: c.discovered_via for c in generate(perm_config(), gh)}
    assert out["bigdatasciencegroup/bytedance-deer-flow"] == "permutation:org-fold"


def test_permutation_queries_use_recency_sort_exact_name_does_not():
    gh = QueryMapGitHub({})
    generate(perm_config(), gh)
    exact_q = next(q for q in gh.queries if q["query"] == "deer-flow in:name")
    fold_q = next(q for q in gh.queries if q["query"] == "bytedance-deer-flow in:name")
    assert exact_q["sort"] is None  # existing path's ordering is untouched
    assert fold_q["sort"] == "updated"  # variant target is a fresh impostor


def test_permutation_keeps_only_exact_variant_matches():
    # A substring-but-not-exact result for the variant query must be dropped.
    noise = search_item("someone", "bytedance-deer-flow-extra")
    gh = QueryMapGitHub({"bytedance-deer-flow in:name": [noise]})
    out = [c.full_name for c in generate(perm_config(), gh)]
    assert "someone/bytedance-deer-flow-extra" not in out


def test_generate_merges_exact_and_permutation_results():
    fold = search_item("bigdatasciencegroup", "bytedance-deer-flow")
    gh = QueryMapGitHub(
        {
            "deer-flow in:name": [search_item("evil", "deer-flow")],
            "bytedance-deer-flow in:name": [fold],
        }
    )
    out = {c.full_name: c.discovered_via for c in generate(perm_config(), gh)}
    assert out["evil/deer-flow"] == "exact-name"
    assert out["bigdatasciencegroup/bytedance-deer-flow"] == "permutation:org-fold"


def test_permutation_respects_max_variants_cap():
    gh = QueryMapGitHub({})
    generate(perm_config(INPUT_MAX_VARIANTS="2"), gh)
    variant_queries = [q for q in gh.queries if q["sort"] == "updated"]
    assert len(variant_queries) == 2  # capped to max-variants, org-fold first


def test_permutation_does_not_rerun_exact_name_query():
    gh = QueryMapGitHub({})
    generate(perm_config(), gh)
    exact_queries = [q for q in gh.queries if q["query"] == "deer-flow in:name"]
    assert len(exact_queries) == 1  # the exact name is never re-queried as a variant
