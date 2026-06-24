"""Tests for the GitHub I/O client — the only network module.

All network access is isolated behind two seams:
- ``_send(method, url, headers, data) -> HttpResponse`` for JSON API calls
- ``_urlopen(request) -> file-like`` for streaming asset downloads

Tests replace those seams with fakes; no real HTTP happens.
"""

import io
import json

import pytest

from repo_impersonation_monitor.github_io import (
    GitHubClient,
    GitHubError,
    HttpResponse,
    NotFoundError,
    parse_next_link,
)


def resp(status=200, body=b"", headers=None):
    return HttpResponse(status=status, headers=headers or {}, body=body)


def json_resp(obj, status=200, headers=None):
    return HttpResponse(status=status, headers=headers or {}, body=json.dumps(obj).encode())


class FakeSender:
    """Replaces GitHubClient._send. Returns queued responses, records calls."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, data):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data})
        if not self._responses:
            raise AssertionError(f"unexpected extra request: {method} {url}")
        return self._responses.pop(0)


def make_client(responses, **kw):
    client = GitHubClient(token="tok_abc", sleep=lambda _s: None, max_sleep=0.0, **kw)
    client._send = FakeSender(responses)
    return client


# --- headers / auth -------------------------------------------------------

def test_auth_and_standard_headers_sent():
    client = make_client([json_resp({"full_name": "a/b"})])
    client.get_repo("a/b")
    sent = client._send.calls[0]["headers"]
    assert sent["Authorization"] == "Bearer tok_abc"
    assert "application/vnd.github+json" in sent["Accept"]
    assert sent["X-GitHub-Api-Version"]
    assert sent["User-Agent"]


def test_no_auth_header_when_no_token():
    client = GitHubClient(token="", sleep=lambda _s: None, max_sleep=0.0)
    client._send = FakeSender([json_resp({"full_name": "a/b"})])
    client.get_repo("a/b")
    assert "Authorization" not in client._send.calls[0]["headers"]


# --- get_repo / releases / tree ------------------------------------------

def test_get_repo_returns_parsed_json():
    client = make_client([json_resp({"full_name": "a/b", "fork": False})])
    data = client.get_repo("a/b")
    assert data["full_name"] == "a/b"
    assert client._send.calls[0]["url"].endswith("/repos/a/b")


def test_get_repo_404_raises_notfound():
    client = make_client([resp(status=404, body=b'{"message":"Not Found"}')])
    with pytest.raises(NotFoundError):
        client.get_repo("a/missing")


def test_list_releases_returns_list():
    client = make_client([json_resp([{"id": 1}, {"id": 2}])])
    rels = client.list_releases("a/b")
    assert len(rels) == 2
    assert client._send.calls[0]["url"].endswith("/repos/a/b/releases")


def test_get_tree_top_level_uses_given_branch():
    client = make_client([json_resp({"tree": [{"path": "src"}, {"path": "README.md"}]})])
    paths = client.get_tree_top_level("a/b", default_branch="dev")
    assert paths == ["src", "README.md"]
    assert "/git/trees/dev" in client._send.calls[0]["url"]


def test_get_tree_top_level_dirs_filters_to_directories():
    client = make_client([json_resp({"tree": [
        {"path": "src", "type": "tree"},
        {"path": "README.md", "type": "blob"},
        {"path": "lib", "type": "tree"},
    ]})])
    dirs = client.get_tree_top_level_dirs("a/b", default_branch="main")
    assert dirs == ["src", "lib"]


def test_get_tree_top_level_fetches_default_branch_when_missing():
    client = make_client([
        json_resp({"default_branch": "trunk"}),       # get_repo
        json_resp({"tree": [{"path": "lib"}]}),         # tree
    ])
    paths = client.get_tree_top_level("a/b")
    assert paths == ["lib"]
    assert "/git/trees/trunk" in client._send.calls[1]["url"]


# --- search pagination ----------------------------------------------------

def test_search_repos_follows_next_link_until_exhausted():
    page1 = json_resp(
        {"items": [{"full_name": "x/1"}, {"full_name": "x/2"}]},
        headers={"link": '<https://api.github.com/search/repositories?q=z&page=2>; rel="next"'},
    )
    page2 = json_resp({"items": [{"full_name": "x/3"}]})  # no next link
    client = make_client([page1, page2])
    items = client.search_repos("z", per_page=2)
    assert [i["full_name"] for i in items] == ["x/1", "x/2", "x/3"]


def test_search_repos_respects_max_pages():
    page = json_resp(
        {"items": [{"full_name": "x/1"}]},
        headers={"link": '<https://api.github.com/search/repositories?q=z&page=99>; rel="next"'},
    )
    # Always returns a next link; max_pages must stop it.
    client = make_client([page, page, page])
    items = client.search_repos("z", per_page=1, max_pages=3)
    assert len(items) == 3
    assert len(client._send.calls) == 3


def test_search_query_encoded_in_url():
    client = make_client([json_resp({"items": []})])
    client.search_repos("foo in:name fork:false")
    url = client._send.calls[0]["url"]
    assert "/search/repositories" in url
    assert "in%3Aname" in url or "in:name" in url


def test_parse_next_link_helper():
    link = (
        '<https://api.github.com/x?page=2>; rel="next", '
        '<https://api.github.com/x?page=9>; rel="last"'
    )
    assert parse_next_link(link) == "https://api.github.com/x?page=2"
    assert parse_next_link("") is None
    assert parse_next_link('<https://api.github.com/x?page=9>; rel="last"') is None


# --- rate limiting / retries ---------------------------------------------

def test_retries_on_secondary_rate_limit_then_succeeds():
    limited = resp(status=403, headers={"retry-after": "0"}, body=b'{"message":"rate limited"}')
    ok = json_resp({"full_name": "a/b"})
    sleeps = []
    client = GitHubClient(token="t", sleep=sleeps.append, max_sleep=5.0)
    client._send = FakeSender([limited, ok])
    data = client.get_repo("a/b")
    assert data["full_name"] == "a/b"
    assert len(sleeps) == 1  # backed off once


def test_retries_on_primary_ratelimit_remaining_zero():
    limited = resp(status=403, headers={"x-ratelimit-remaining": "0", "retry-after": "0"})
    ok = json_resp({"ok": True})
    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0)
    client._send = FakeSender([limited, ok])
    assert client.get_repo("a/b") == {"ok": True}


def test_403_without_ratelimit_is_not_retried():
    # A permissions 403 (remaining > 0, no Retry-After) should fail fast.
    forbidden = resp(status=403, headers={"x-ratelimit-remaining": "42"},
                     body=b'{"message":"Resource not accessible"}')
    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0)
    client._send = FakeSender([forbidden])
    with pytest.raises(GitHubError):
        client.get_repo("a/b")


def test_retries_on_5xx_then_gives_up():
    five = resp(status=503, body=b"oops")
    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0, max_retries=2)
    client._send = FakeSender([five, five, five])  # max_retries+1 attempts
    with pytest.raises(GitHubError):
        client.get_repo("a/b")
    assert len(client._send.calls) == 3


# --- asset download (size cap) -------------------------------------------

def test_download_asset_returns_bytes_under_cap():
    payload = b"MZ" + b"\x00" * 100
    client = GitHubClient(token="t")
    client._urlopen = lambda request: io.BytesIO(payload)
    data = client.download_asset("https://example.com/a.exe", max_bytes=1024)
    assert data == payload


def test_download_asset_over_cap_raises():
    payload = b"x" * 5000
    client = GitHubClient(token="t")
    client._urlopen = lambda request: io.BytesIO(payload)
    with pytest.raises(GitHubError):
        client.download_asset("https://example.com/big.exe", max_bytes=1000)


# --- issues: create + dedupe ---------------------------------------------

def test_open_issue_posts_and_returns_issue():
    client = make_client([json_resp({"number": 7, "html_url": "u"}, status=201)])
    out = client.open_issue("a/b", "Title", "Body", labels=["x"])
    call = client._send.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/repos/a/b/issues")
    payload = json.loads(call["data"])
    assert payload["title"] == "Title"
    assert payload["labels"] == ["x"]
    assert out["number"] == 7


def test_find_existing_issue_matches_marker():
    issues = [
        {"number": 1, "body": "unrelated"},
        {"number": 2, "body": "header\n<!-- rim:evil/x -->\nmore"},
    ]
    client = make_client([json_resp(issues)])
    found = client.find_existing_issue("a/b", "<!-- rim:evil/x -->")
    assert found["number"] == 2


def test_find_existing_issue_returns_none_when_absent():
    client = make_client([json_resp([{"number": 1, "body": "nope"}])])
    assert client.find_existing_issue("a/b", "<!-- rim:evil/x -->") is None


def test_find_existing_issue_handles_null_body():
    client = make_client([json_resp([{"number": 1, "body": None}])])
    assert client.find_existing_issue("a/b", "<!-- rim:evil/x -->") is None


# --- _send seam (exercises the urllib boundary via a fake _urlopen) -------

class _FakeHTTPResponse:
    """Minimal stand-in for urllib's response object (context manager)."""

    def __init__(self, status, headers, body):
        self.status = status
        self.headers = _FakeHeaders(headers)
        self._body = body

    def read(self, *_):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeHeaders:
    def __init__(self, items):
        self._items = list(items.items())

    def items(self):
        return self._items


def test_send_success_lowercases_headers():
    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0)
    client._urlopen = lambda req: _FakeHTTPResponse(
        200, {"X-RateLimit-Remaining": "9", "Link": "x"}, b'{"ok": true}'
    )
    data = client.get_repo("a/b")
    assert data == {"ok": True}


def test_send_maps_httperror_to_response():
    import urllib.error

    def raise_http_error(req):
        raise urllib.error.HTTPError(
            url="u", code=404, msg="nf",
            hdrs=_FakeHeaders({"X-Foo": "bar"}), fp=io.BytesIO(b'{"message":"Not Found"}'),
        )

    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0)
    client._urlopen = raise_http_error
    with pytest.raises(NotFoundError):
        client.get_repo("a/missing")


# --- _retry_wait branch coverage -----------------------------------------

def test_retry_wait_uses_ratelimit_reset_when_no_retry_after():
    client = GitHubClient(token="t", max_sleep=100.0, clock=lambda: 1000.0)
    r = resp(status=403, headers={"x-ratelimit-reset": "1010"})
    assert client._retry_wait(r, attempt=0) == pytest.approx(10.0)


def test_retry_wait_exponential_backoff_fallback():
    client = GitHubClient(token="t", max_sleep=100.0)
    r = resp(status=503)  # no rate-limit headers -> exp backoff
    assert client._retry_wait(r, attempt=3) == pytest.approx(8.0)


def test_retry_wait_capped_at_max_sleep():
    client = GitHubClient(token="t", max_sleep=5.0)
    r = resp(status=403, headers={"retry-after": "999"})
    assert client._retry_wait(r, attempt=0) == 5.0


def test_error_message_extracted_from_body():
    bad = resp(status=403, headers={"x-ratelimit-remaining": "5"},
               body=b'{"message":"Resource not accessible by integration"}')
    client = GitHubClient(token="t", sleep=lambda _s: None, max_sleep=0.0)
    client._send = FakeSender([bad])
    with pytest.raises(GitHubError, match="Resource not accessible"):
        client.get_repo("a/b")
