"""URL fetching, with a raw-response cache.

The cache stores exactly what came off the wire, keyed by URL. Re-running
against the same article — while tuning normalization rules, say — costs no
network and no rate limit. `--no-cache` skips the read but still refreshes the
entry, so the next run is fast again.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from readaloud import __version__
from readaloud.config import cache_home
from readaloud.errors import FetchError

TIMEOUT_SECONDS = 30.0

# A real browser UA. Plenty of publishers serve a stub or a 403 to anything
# that announces itself as a script, which turns into a confusing "no article
# text" error three stages later.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Wikipedia and other Wikimedia sites do the opposite of the publishers: they
# 403 generic browser UAs used by automated clients, and accept a descriptive
# one that says who is calling. So a 403 earns exactly one retry with this.
# Built from __version__ rather than a literal, so it can't go stale the way
# a hardcoded one already had (0.1.0, and the wrong GitHub username) by the
# time this was next touched.
DESCRIPTIVE_USER_AGENT = f"readaloud/{__version__} (+https://github.com/Nornchan/readaloud)"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


@dataclass
class Response:
    """A fetched document, from the network or from the cache."""

    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    from_cache: bool = False

    @property
    def is_pdf(self) -> bool:
        return "pdf" in self.content_type.lower() or self.body[:5] == b"%PDF-"

    def text(self) -> str:
        """Decode the body, trusting the charset header and falling back."""
        encoding = None
        if "charset=" in self.content_type:
            encoding = self.content_type.split("charset=", 1)[1].split(";")[0].strip()
        for candidate in (encoding, "utf-8", "cp1252", "latin-1"):
            if not candidate:
                continue
            try:
                return self.body.decode(candidate)
            except (UnicodeDecodeError, LookupError):
                continue
        return self.body.decode("utf-8", errors="replace")


def cache_dir() -> Path:
    return cache_home() / "fetch"


def _key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def _read_cache(url: str) -> Response | None:
    base = cache_dir() / _key(url)
    meta_path = base.with_suffix(".json")
    body_path = base.with_suffix(".body")
    if not (meta_path.exists() and body_path.exists()):
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        body = body_path.read_bytes()
    except (OSError, json.JSONDecodeError):
        return None  # A corrupt cache entry is not worth failing the run over.
    return Response(
        url=url,
        final_url=meta.get("final_url", url),
        status=meta.get("status", 200),
        content_type=meta.get("content_type", ""),
        body=body,
        from_cache=True,
    )


def _write_cache(response: Response) -> None:
    base = cache_dir() / _key(response.url)
    try:
        base.parent.mkdir(parents=True, exist_ok=True)
        base.with_suffix(".body").write_bytes(response.body)
        base.with_suffix(".json").write_text(
            json.dumps(
                {
                    "url": response.url,
                    "final_url": response.final_url,
                    "status": response.status,
                    "content_type": response.content_type,
                    "fetched_at": time.time(),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass  # A cache we can't write is a slow tool, not a broken one.


def _explain_status(status: int, url: str) -> FetchError:
    if status in (401, 402):
        return FetchError(
            f"{url} requires a subscription or login (HTTP {status})",
            "readaloud cannot sign in for you. Save the article as HTML from "
            "your browser and pass the file instead.",
        )
    if status == 403:
        return FetchError(
            f"{url} refused the request (HTTP 403)",
            "The site is blocking automated fetches. Save the page as HTML "
            "from your browser and pass the file instead.",
        )
    if status == 404:
        return FetchError(f"{url} does not exist (HTTP 404)", "Check the URL.")
    if status == 429:
        return FetchError(
            f"{url} is rate-limiting this client (HTTP 429)",
            "Wait a minute and try again.",
        )
    if 500 <= status < 600:
        return FetchError(
            f"{url} returned a server error (HTTP {status})",
            "The site is having trouble. Try again later.",
        )
    return FetchError(f"{url} returned HTTP {status}", "Check the URL.")


def _get(url: str, headers: dict[str, str]) -> httpx.Response:
    with httpx.Client(
        headers=headers,
        follow_redirects=True,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        return client.get(url)


def fetch(url: str, use_cache: bool = True) -> Response:
    """GET `url`, following redirects. Raises FetchError with an actionable hint."""
    if use_cache:
        cached = _read_cache(url)
        if cached is not None:
            return cached

    try:
        raw = _get(url, HEADERS)
        if raw.status_code == 403:
            raw = _get(url, {**HEADERS, "User-Agent": DESCRIPTIVE_USER_AGENT})
    except httpx.TimeoutException as exc:
        raise FetchError(
            f"{url} did not respond within {TIMEOUT_SECONDS:.0f} seconds",
            "Check your connection, or try again later.",
        ) from exc
    except httpx.TooManyRedirects as exc:
        raise FetchError(
            f"{url} redirected too many times",
            "The site may be redirecting to a consent or login page.",
        ) from exc
    except httpx.HTTPError as exc:
        raise FetchError(
            f"could not reach {url}: {exc}",
            "Check the URL and your network connection.",
        ) from exc

    if raw.status_code >= 400:
        raise _explain_status(raw.status_code, url)

    response = Response(
        url=url,
        final_url=str(raw.url),
        status=raw.status_code,
        content_type=raw.headers.get("content-type", ""),
        body=raw.content,
    )
    _write_cache(response)
    return response
