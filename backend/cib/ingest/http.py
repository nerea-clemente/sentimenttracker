"""Minimal HTTP GET, stdlib only, so the core tool needs no third-party install."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "campaign-impact-benchmarker/0.1 (media monitoring; contact your admin)"
TIMEOUT = 30


class FetchError(RuntimeError):
    pass


def get(url: str, params: dict | None = None, timeout: int = TIMEOUT) -> str:
    full = url
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        full = f"{url}{'&' if '?' in url else '?'}{query}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code} from {full}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"Could not reach {full}: {exc}") from exc


def get_json(url: str, params: dict | None = None, timeout: int = TIMEOUT):
    body = get(url, params, timeout)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} did not return JSON: {body[:200]!r}") from exc
