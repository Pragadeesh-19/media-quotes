from datetime import UTC, datetime

import httpx

from mqc.discovery import parse_rss
from mqc.extract import extract_article
from mqc.fetch import FetchRequest, Fetched, fetch_article
from mqc.net import Http
from mqc.syndication import check_partner_page, lead_probe
from tests.conftest import FIXTURES

AOL_URL = "https://www.aol.com/articles/ukraine-main-topic-trump-putin-063820000.html"
REUTERS_URL = "https://www.reuters.com/world/china/ukraine-was-main-topic-trump-putin-meeting-kremlin-says-2026-11-19/"


def _reuters_candidate():
    items = parse_rss((FIXTURES / "bing_reuters_rss.xml").read_text(encoding="utf-8"), "reuters", "bing_news")
    return next(i for i in items if i.url == REUTERS_URL)


def test_probe_drops_dateline_and_truncation():
    assert lead_probe("MOSCOW, Nov 19 (Reuters) - Ukraine struck the bridge linking Russia and Crimea for the third "
                      "time in a week ...") == "Ukraine struck the bridge linking Russia and Crimea for the third time"


def test_short_lead_gives_no_probe():
    assert lead_probe("Kyiv was hit.") is None


def test_aol_copy_with_reuters_credit_and_lead_is_accepted():
    probe = lead_probe(_reuters_candidate().summary)
    html = (FIXTURES / "aol_reuters.html").read_text(encoding="utf-8")
    assert check_partner_page(html, "www.aol.com", "div.content-body", probe) is None


def test_page_whose_canonical_points_elsewhere_is_rejected():
    probe = lead_probe(_reuters_candidate().summary)
    html = (FIXTURES / "yahoo_reuters.html").read_text(encoding="utf-8")
    assert check_partner_page(html, "uk.news.yahoo.com", None, probe) == "canonical link is not on the partner domain"


def test_partner_page_without_the_reuters_lead_is_rejected():
    html = (FIXTURES / "aol_reuters.html").read_text(encoding="utf-8")
    probe = "Ukraine struck the bridge linking Russia and Crimea for the third time"
    assert check_partner_page(html, "www.aol.com", "div.content-body", probe) == (
        "Reuters lead not found at the start of the article")


def test_syndicated_article_keeps_reuters_link_title_and_date(sources):
    candidate = _reuters_candidate()
    html = (FIXTURES / "aol_reuters.html").read_text(encoding="utf-8")

    article = extract_article(html, candidate, sources["reuters"], AOL_URL, "syndication", "div.content-body")

    assert article.url == REUTERS_URL
    assert article.title == candidate.title
    assert article.published_at == datetime(2026, 11, 19, 0, 36, tzinfo=UTC)
    assert article.published_at_source == "feed"
    assert article.blocks[0].text.startswith("MOSCOW, Nov 19 (Reuters) - The war in Ukraine dominated")
    assert not any(b.text.startswith(("(Reporting", "Advertisement")) for b in article.blocks)


def test_fetch_chain_falls_through_to_partner_copy(sources):
    candidate = _reuters_candidate()
    search = ('<rss><channel><item><title>t</title><link>http://www.bing.com/news/apiclick.aspx?url='
              'https%3a%2f%2fwww.aol.com%2farticles%2fukraine-main-topic-trump-putin-063820000.html</link>'
              '</item></channel></rss>')
    aol = (FIXTURES / "aol_reuters.html").read_text(encoding="utf-8")

    def handler(request):
        url = str(request.url)
        if "bing.com/news/search" in url:
            return httpx.Response(200, text=search)
        if url == AOL_URL:
            return httpx.Response(200, text=aol)
        if "cdx/search" in url:
            return httpx.Response(200, text="")
        return httpx.Response(401)

    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    request = FetchRequest(url=REUTERS_URL, lead=candidate.summary, partners=sources["reuters"].syndication_partners)

    result = fetch_article(request, sources["reuters"].fetch_methods, http)

    assert isinstance(result, Fetched)
    assert (result.method, result.retrieval_url, result.body_selector) == ("syndication", AOL_URL, "div.content-body")
