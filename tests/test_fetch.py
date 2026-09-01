import httpx
import pytest

from readaloud import fetch as fetch_module
from readaloud.errors import FetchError
from readaloud.fetch import Response, fetch

URL = "https://example.com/article"


class FakeResponse:
    def __init__(self, status=200, body=b"<html>hi</html>", content_type="text/html"):
        self.status_code = status
        self.content = body
        self.url = URL
        self.headers = {"content-type": content_type}


def serve(monkeypatch, response, counter=None):
    def get(self, url, **kwargs):
        if counter is not None:
            counter.append(url)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(httpx.Client, "get", get)


def test_fetch_and_cache_round_trip(monkeypatch):
    calls = []
    serve(monkeypatch, FakeResponse(), calls)

    first = fetch(URL)
    assert first.from_cache is False
    assert first.body == b"<html>hi</html>"

    second = fetch(URL)
    assert second.from_cache is True
    assert second.body == first.body
    assert len(calls) == 1, "the second fetch should not have hit the network"


def test_no_cache_bypasses_the_read_but_refreshes(monkeypatch):
    calls = []
    serve(monkeypatch, FakeResponse(body=b"one"), calls)
    fetch(URL)

    serve(monkeypatch, FakeResponse(body=b"two"), calls)
    fresh = fetch(URL, use_cache=False)
    assert fresh.body == b"two"
    assert fresh.from_cache is False

    assert fetch(URL).body == b"two", "the refreshed body should be cached"
    assert len(calls) == 2


def test_a_corrupt_cache_entry_is_ignored(monkeypatch):
    serve(monkeypatch, FakeResponse())
    fetch(URL)
    for entry in fetch_module.cache_dir().glob("*.json"):
        entry.write_text("{not json")
    serve(monkeypatch, FakeResponse(body=b"refetched"))
    assert fetch(URL).body == b"refetched"


def test_browser_user_agent_is_sent():
    assert "Mozilla/5.0" in fetch_module.HEADERS["User-Agent"]
    assert "Chrome" in fetch_module.HEADERS["User-Agent"]


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "subscription or login"),
        (403, "refused the request"),
        (404, "does not exist"),
        (429, "rate-limiting"),
        (503, "server error"),
    ],
)
def test_status_codes_get_their_own_messages(monkeypatch, status, expected):
    serve(monkeypatch, FakeResponse(status=status))
    with pytest.raises(FetchError) as caught:
        fetch(URL)
    assert expected in caught.value.message
    assert caught.value.hint


def test_403_is_retried_with_a_descriptive_user_agent(monkeypatch):
    """Wikimedia sites 403 browser UAs and accept a UA that says who is calling."""
    seen = []

    def get(self, url, **kwargs):
        agent = self.headers["user-agent"]
        seen.append(agent)
        if "Mozilla" in agent:
            return FakeResponse(status=403, body=b"blocked")
        return FakeResponse(body=b"<html>the article</html>")

    monkeypatch.setattr(httpx.Client, "get", get)
    assert fetch(URL).body == b"<html>the article</html>"
    assert len(seen) == 2
    assert "readaloud/" in seen[1]


def test_403_that_survives_the_retry_is_reported(monkeypatch):
    serve(monkeypatch, FakeResponse(status=403))
    with pytest.raises(FetchError, match="refused the request"):
        fetch(URL)


def test_timeout_says_so(monkeypatch):
    serve(monkeypatch, httpx.ConnectTimeout("slow"))
    with pytest.raises(FetchError, match="did not respond within 30 seconds"):
        fetch(URL)


def test_connection_failure(monkeypatch):
    serve(monkeypatch, httpx.ConnectError("no route"))
    with pytest.raises(FetchError, match="could not reach"):
        fetch(URL)


def test_pdf_is_recognised_by_content_type_or_bytes():
    assert Response(URL, URL, 200, "application/pdf", b"junk").is_pdf
    assert Response(URL, URL, 200, "application/octet-stream", b"%PDF-1.7").is_pdf
    assert not Response(URL, URL, 200, "text/html", b"<html>").is_pdf


def test_decoding_falls_back_when_the_charset_lies():
    response = Response(URL, URL, 200, "text/html; charset=utf-8", b"caf\xe9")
    assert "caf" in response.text()
