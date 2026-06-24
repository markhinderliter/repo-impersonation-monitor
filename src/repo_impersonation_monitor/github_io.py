"""The only network module: a thin GitHub REST/Search client.

Everything else in the package is pure and takes plain data. All HTTP lives here,
behind two small seams that tests replace:

- ``_send(method, url, headers, data) -> HttpResponse`` for JSON API calls
- ``_urlopen(request) -> file-like`` for streaming (size-capped) asset downloads

Rate-limit awareness matters: the Search API bucket is only ~30 req/min
(verified live), separate from the 5000/hr core bucket. On a rate-limit or 5xx
response the client backs off and retries; a permissions 403 fails fast.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

API_VERSION = "2022-11-28"
USER_AGENT = "repo-impersonation-monitor"
BASE_URL = "https://api.github.com"
_DOWNLOAD_CHUNK = 64 * 1024
_RETRY_STATUS = frozenset({500, 502, 503, 504})
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class GitHubError(Exception):
    """A non-retryable or exhausted GitHub API error."""


class NotFoundError(GitHubError):
    """A 404 from the API."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def parse_next_link(link_header: str | None) -> str | None:
    """Extract the rel=next URL from a Link header, or None."""
    if not link_header:
        return None
    match = _NEXT_LINK_RE.search(link_header)
    return match.group(1) if match else None


_ALLOWED_URL_SCHEMES = ("http", "https")


def _require_web_url(url: str) -> None:
    """Reject non-http(s) URLs before they reach urlopen.

    A release asset's download URL crosses the hostile-artifact trust boundary
    (it comes from an untrusted candidate repo). Refusing schemes like ``file://``
    or ``ftp://`` keeps a crafted URL from reading local files or reaching
    unexpected services.
    """
    if urlsplit(url).scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise GitHubError(f"refusing to open non-http(s) URL: {url!r}")


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = BASE_URL,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        max_sleep: float = 60.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._sleep = sleep
        self._max_sleep = max_sleep
        self._clock = clock

    # --- seams (the only real network access) ----------------------------

    def _send(self, method: str, url: str, headers: dict, data: bytes | None) -> HttpResponse:
        request = urllib.request.Request(url=url, method=method, headers=headers, data=data)
        try:
            with self._urlopen(request) as response:
                return HttpResponse(
                    status=response.status,
                    headers=self._lower_headers(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as exc:
            # HTTPError is itself a response-like object for 4xx/5xx.
            return HttpResponse(
                status=exc.code,
                headers=self._lower_headers(exc.headers.items() if exc.headers else []),
                body=exc.read() if hasattr(exc, "read") else b"",
            )

    def _urlopen(self, request):  # pragma: no cover - thin wrapper over stdlib
        # Scheme is validated to http(s) by _require_web_url before opening.
        _require_web_url(request.full_url)
        return urllib.request.urlopen(request)  # nosec B310

    # --- helpers ---------------------------------------------------------

    @staticmethod
    def _lower_headers(items: Iterable[tuple[str, str]]) -> dict:
        return {k.lower(): v for k, v in items}

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _is_rate_limited(self, response: HttpResponse) -> bool:
        if response.status == 429:
            return True
        if response.status == 403:
            # Distinguish a rate-limit 403 from a permissions 403.
            if response.header("retry-after") is not None:
                return True
            if response.header("x-ratelimit-remaining") == "0":
                return True
        return False

    def _retry_wait(self, response: HttpResponse, attempt: int) -> float:
        retry_after = response.header("retry-after")
        if retry_after is not None:
            try:
                return min(float(retry_after), self._max_sleep)
            except ValueError:
                pass
        reset = response.header("x-ratelimit-reset")
        if reset is not None:
            try:
                return max(0.0, min(float(reset) - self._clock(), self._max_sleep))
            except ValueError:
                pass
        return min(float(2**attempt), self._max_sleep)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> HttpResponse:
        url = path if path.startswith("http") else f"{self._base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        headers = self._headers()
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        for attempt in range(self._max_retries + 1):
            response = self._send(method, url, headers, data)
            if response.status in (200, 201):
                return response
            if response.status == 404:
                raise NotFoundError(f"404 Not Found: {method} {url}")

            retryable = self._is_rate_limited(response) or response.status in _RETRY_STATUS
            if retryable and attempt < self._max_retries:
                self._sleep(self._retry_wait(response, attempt))
                continue
            raise GitHubError(
                f"{response.status} from {method} {url}: {self._message(response)}"
            )
        raise GitHubError(f"retries exhausted: {method} {url}")

    @staticmethod
    def _message(response: HttpResponse) -> str:
        try:
            return json.loads(response.body).get("message", "")
        except (ValueError, AttributeError):
            return ""

    def _get_json(self, path: str, *, params: dict | None = None):
        return json.loads(self._request("GET", path, params=params).body)

    # --- public API ------------------------------------------------------

    def search_repos(
        self, query: str, *, per_page: int = 100, max_pages: int = 10
    ) -> list[dict]:
        """Search repositories, following rel=next up to ``max_pages``.

        Each page is one request against the ~30/min search bucket — keep
        ``max_pages`` small. GitHub also caps search at 1000 results total.
        """
        items: list[dict] = []
        response = self._request(
            "GET",
            "/search/repositories",
            params={"q": query, "per_page": per_page},
        )
        pages = 1
        while True:
            payload = json.loads(response.body)
            items.extend(payload.get("items", []))
            next_url = parse_next_link(response.header("link"))
            if not next_url or pages >= max_pages:
                break
            response = self._request("GET", next_url)
            pages += 1
        return items

    def get_repo(self, full_name: str) -> dict:
        return self._get_json(f"/repos/{full_name}")

    def list_releases(self, full_name: str) -> list[dict]:
        return self._get_json(f"/repos/{full_name}/releases")

    def get_tree_top_level(self, full_name: str, default_branch: str | None = None) -> list[str]:
        """Return top-level path names of a repo's default branch tree."""
        if default_branch is None:
            default_branch = self.get_repo(full_name).get("default_branch", "main")
        tree = self._get_json(f"/repos/{full_name}/git/trees/{default_branch}")
        return [entry["path"] for entry in tree.get("tree", []) if "path" in entry]

    def get_tree_top_level_dirs(
        self, full_name: str, default_branch: str | None = None
    ) -> list[str]:
        """Return only the top-level *directory* names (tree entries)."""
        if default_branch is None:
            default_branch = self.get_repo(full_name).get("default_branch", "main")
        tree = self._get_json(f"/repos/{full_name}/git/trees/{default_branch}")
        return [
            entry["path"]
            for entry in tree.get("tree", [])
            if "path" in entry and entry.get("type") == "tree"
        ]

    def download_asset(self, url: str, *, max_bytes: int) -> bytes:
        """Stream a release asset into memory, aborting if it exceeds ``max_bytes``.

        The bytes are returned for parsing only — the caller never executes them.
        """
        _require_web_url(url)  # hostile asset URL — reject file://, ftp://, etc.
        request = urllib.request.Request(url=url, headers=self._headers())
        chunks: list[bytes] = []
        total = 0
        with self._urlopen(request) as response:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise GitHubError(
                        f"asset exceeds size cap of {max_bytes} bytes: {url}"
                    )
                chunks.append(chunk)
        return b"".join(chunks)

    def open_issue(
        self, repo: str, title: str, body: str, labels: Sequence[str] | None = None
    ) -> dict:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = list(labels)
        return json.loads(self._request("POST", f"/repos/{repo}/issues", json_body=payload).body)

    def find_existing_issue(
        self, repo: str, marker: str, *, max_pages: int = 5, per_page: int = 100
    ) -> dict | None:
        """Find an open-or-closed issue whose body contains ``marker`` (dedupe)."""
        for page in range(1, max_pages + 1):
            issues = self._get_json(
                f"/repos/{repo}/issues",
                params={"state": "all", "per_page": per_page, "page": page},
            )
            if not issues:
                return None
            for issue in issues:
                if marker in (issue.get("body") or ""):
                    return issue
            if len(issues) < per_page:
                return None
        return None
