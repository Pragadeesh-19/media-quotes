import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from mqc.config import Channel, SourceConfig
from mqc.discovery import parse_rss
from mqc.extract import ExtractionError, extract_article
from mqc.fetch import FetchRequest, Unreachable, fetch_article
from mqc.models import Candidate
from mqc.net import Http, HttpFailure

COMMON_FEED_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/index.xml", "/news/rss")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")


class OnboardingError(Exception):
    pass


@dataclass(frozen=True)
class Onboarding:
    config: SourceConfig
    sample: Candidate
    sample_blocks: int


def profile_source(homepage: str, http: Http, source_id: str | None = None, name: str | None = None) -> Onboarding:
    """Build a source config for an outlet from its homepage: find its feeds, then prove one article extracts."""
    try:
        response = http.get(homepage)
    except HttpFailure as error:
        raise OnboardingError(f"homepage unreachable: {error}") from error
    if response.status_code != 200:
        raise OnboardingError(f"homepage returned HTTP {response.status_code}")
    base = str(response.url)
    soup = BeautifulSoup(response.text, "lxml")
    site = _bare_host(base)
    feeds = _advertised_feeds(soup, base) or [urljoin(base, path) for path in COMMON_FEED_PATHS]

    working: dict[str, list[Candidate]] = {}
    tried = []
    for feed in dict.fromkeys(feeds):
        items = _read_feed(feed, http, tried)
        own = [i for i in items if _bare_host(i.url) == site]
        if own:
            working[feed] = own
        elif items:
            tried.append(f"{feed}: items are not on {site}")
    if not working:
        raise OnboardingError(f"no readable RSS feed for {site}: {'; '.join(tried)}")

    hosts = sorted({urlsplit(i.url).netloc.lower() for items in working.values() for i in items})
    site_name = name or _site_name(soup) or site
    config = SourceConfig(
        source_id=source_id or re.sub(r"[^a-z0-9]+", "", site.split(".")[0]),
        name=site_name,
        publication_name=site_name,
        domains=hosts,
        channels=[Channel(type="rss", url=feed) for feed in working],
        history_channels=[Channel(type="wayback_feed", url=feed) for feed in working],
        fetch_methods=["direct", "wayback"],
    )
    sample = next(iter(working.values()))[0]
    return Onboarding(config, sample, _sample_blocks(sample, config, http))


def _advertised_feeds(soup: BeautifulSoup, base: str) -> list[str]:
    links = soup.find_all("link", rel="alternate")
    return [urljoin(base, link["href"]) for link in links if link.get("type") in FEED_TYPES and link.get("href")]


def _read_feed(feed: str, http: Http, tried: list[str]) -> list[Candidate]:
    try:
        response = http.get(feed)
    except HttpFailure as error:
        tried.append(f"{feed}: {error}")
        return []
    if response.status_code != 200:
        tried.append(f"{feed}: HTTP {response.status_code}")
        return []
    items = parse_rss(response.text, "probe", "rss")
    if not items:
        tried.append(f"{feed}: no feed items")
    return items


def _sample_blocks(sample: Candidate, config: SourceConfig, http: Http) -> int:
    result = fetch_article(FetchRequest(sample.url), config.fetch_methods, http)
    if isinstance(result, Unreachable):
        raise OnboardingError(f"sample article {sample.url} unreachable: {result.reason}")
    try:
        article = extract_article(result.html, sample.model_copy(update={"source_id": config.source_id}), config,
                                  result.retrieval_url, result.method)
    except ExtractionError as error:
        raise OnboardingError(f"sample article {sample.url} could not be extracted: {error}") from error
    return len(article.blocks)


def _site_name(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", property="og:site_name")
    return meta.get("content", "").strip() or None if meta else None


def _bare_host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")
