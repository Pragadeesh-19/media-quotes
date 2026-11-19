import httpx
import pytest

from mqc.net import Http
from mqc.onboard import OnboardingError, profile_source
from tests.conftest import FIXTURES

HOMEPAGE = """<html><head><meta property="og:site_name" content="BBC News">
<link rel="alternate" type="application/rss+xml" href="/rss/europe.xml"></head><body></body></html>"""


def _http(routes: dict[str, httpx.Response]) -> Http:
    def handler(request):
        url = str(request.url).split("?")[0]
        return routes.get(url, httpx.Response(404))
    return Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_advertised_feed_and_sample_article_produce_a_config():
    rss = (FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8")
    article = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")
    http = _http({
        "https://www.bbc.co.uk/": httpx.Response(200, text=HOMEPAGE),
        "https://www.bbc.co.uk/rss/europe.xml": httpx.Response(200, text=rss),
        "https://www.bbc.co.uk/news/articles/cm879dqn3e1jo": httpx.Response(
            200, text=article, headers={"content-type": "text/html"}),
    })

    onboarding = profile_source("https://www.bbc.co.uk/", http)

    config = onboarding.config
    assert (config.source_id, config.name) == ("bbc", "BBC News")
    assert config.domains == ["www.bbc.co.uk"]
    assert [c.url for c in config.channels] == ["https://www.bbc.co.uk/rss/europe.xml"]
    assert [c.type for c in config.history_channels] == ["wayback_feed"]
    assert onboarding.sample_blocks > 10


def test_common_feed_paths_are_tried_when_none_is_advertised():
    rss = (FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8")
    article = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")
    http = _http({
        "https://www.bbc.co.uk/": httpx.Response(200, text="<html><body>home</body></html>"),
        "https://www.bbc.co.uk/rss.xml": httpx.Response(200, text=rss),
        "https://www.bbc.co.uk/news/articles/cm879dqn3e1jo": httpx.Response(
            200, text=article, headers={"content-type": "text/html"}),
    })
    assert profile_source("https://www.bbc.co.uk/", http).config.channels[0].url == "https://www.bbc.co.uk/rss.xml"


def test_blocked_homepage_is_reported():
    http = _http({"https://www.understandingwar.org/": httpx.Response(403)})
    with pytest.raises(OnboardingError, match="homepage returned HTTP 403"):
        profile_source("https://www.understandingwar.org/", http)


def test_site_without_any_feed_lists_what_was_tried():
    http = _http({"https://example.org/": httpx.Response(200, text="<html></html>")})
    with pytest.raises(OnboardingError) as error:
        profile_source("https://example.org/", http)
    assert "https://example.org/feed: HTTP 404" in str(error.value)
    assert "https://example.org/rss.xml: HTTP 404" in str(error.value)
