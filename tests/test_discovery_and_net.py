import httpx
import pytest

from mqc.discovery import accept_url, discover, parse_rss, unwrap_redirect
from mqc.fetch import FetchRequest, Fetched, Unreachable, fetch_article
from mqc.net import Http, HttpFailure
from tests.conftest import FIXTURES


def _http(handler) -> tuple[Http, list[float]]:
    sleeps: list[float] = []
    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append,
                clock=lambda: 1000.0, jitter=lambda: 1.0)
    return http, sleeps


def test_bbc_rss_items_parse_with_dates():
    items = parse_rss((FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8"), "bbc", "rss")
    first = items[0]
    assert first.title == "Crimean Bridge attacked again, Russia says"
    assert first.url.startswith("https://www.bbc.co.uk/news/articles/cm879dqn3e1jo")
    assert first.published_at is not None and first.published_at.tzinfo is not None


def test_bing_links_unwrap_to_publisher_urls():
    items = parse_rss((FIXTURES / "bing_reuters_rss.xml").read_text(encoding="utf-8"), "reuters", "bing_news")
    assert items[0].url == ("https://www.reuters.com/world/china/third-attack-week-suspends-traffic-over-crimean-"
                            "bridge-russian-official-says-2026-11-19/")
    assert all("bing.com" not in i.url for i in items)


def test_non_bing_links_are_unchanged():
    assert unwrap_redirect("https://www.bbc.co.uk/news/articles/x") == "https://www.bbc.co.uk/news/articles/x"


def test_excluded_and_foreign_urls_are_dropped(sources):
    assert not accept_url("https://www.bbc.co.uk/sport/football/articles/x", sources["bbc"])
    assert not accept_url("https://www.reuters.com/video/watch/idRW1/", sources["reuters"])
    assert not accept_url("https://example.com/news/x", sources["bbc"])
    assert accept_url("https://www.bbc.co.uk/news/articles/x", sources["bbc"])


def test_failing_channel_is_reported_not_raised(sources):
    xml = (FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8")

    def handler(request):
        return httpx.Response(200, text=xml) if "world/europe" in str(request.url) else httpx.Response(404)

    http, _ = _http(handler)
    report = discover(sources["bbc"], http)
    assert report.candidates
    assert sum(1 for n in report.per_channel.values() if n == 0) == 2
    assert len(report.errors) == 2


def test_retries_on_429_and_honours_retry_after():
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, text="ok")])
    http, sleeps = _http(lambda request: next(responses))
    assert http.get("https://a.example/x").text == "ok"
    assert 7.0 in sleeps


def test_gives_up_after_max_retries_with_failure():
    http, sleeps = _http(lambda request: httpx.Response(503))
    with pytest.raises(HttpFailure, match="HTTP 503"):
        http.get("https://a.example/x")
    assert len([s for s in sleeps if s >= 2.0]) == http.max_retries


def test_network_errors_are_retried_then_raised():
    def handler(request):
        raise httpx.ConnectError("boom")

    http, _ = _http(handler)
    with pytest.raises(HttpFailure, match="ConnectError"):
        http.get("https://a.example/x")


def test_blocked_direct_falls_back_to_archive_snapshot():
    def handler(request):
        url = str(request.url)
        if "cdx/search" in url:
            return httpx.Response(200, text="20261118120000\n")
        if "web.archive.org/web/20261118120000id_/" in url:
            return httpx.Response(200, text="<html>archived</html>", headers={"content-type": "text/html"})
        return httpx.Response(401)

    http, _ = _http(handler)
    result = fetch_article(FetchRequest("https://www.reuters.com/world/x-2026-11-18/"), ["direct", "wayback"], http)
    assert isinstance(result, Fetched)
    assert result.method == "wayback"


def test_no_route_gives_unreachable_with_every_reason():
    def handler(request):
        return httpx.Response(200, text="") if "cdx/search" in str(request.url) else httpx.Response(401)

    http, _ = _http(handler)
    result = fetch_article(FetchRequest("https://www.reuters.com/world/x/"), ["direct", "wayback"], http)
    assert result == Unreachable("direct: HTTP 401; wayback: no successful archive snapshot")
