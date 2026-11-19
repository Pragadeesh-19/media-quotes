import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, quote_plus, urlsplit

from bs4 import BeautifulSoup

from mqc.config import Channel, SourceConfig
from mqc.extract import canonical_url, normalize_text
from mqc.models import Candidate
from mqc.net import Http, HttpFailure

BING_NEWS_RSS = "https://www.bing.com/news/search?q={query}&format=rss&count=100"
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"


@dataclass
class DiscoveryReport:
    candidates: list[Candidate] = field(default_factory=list)
    per_channel: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def discover(config: SourceConfig, http: Http) -> DiscoveryReport:
    return _collect(config, config.channels, lambda channel: _fetch_channel(channel, config, http))


def discover_period(config: SourceConfig, http: Http, start: date, end: date) -> DiscoveryReport:
    """Candidates published between start and end (inclusive), from history channels and current channels."""
    first = datetime.combine(start, time.min, tzinfo=UTC)
    last = datetime.combine(end, time.max, tzinfo=UTC)

    def fetch(channel: Channel) -> list[Candidate]:
        if channel.type == "wayback_feed":
            found = _archived_feed(channel, config, http, start, end)
        else:
            found = _fetch_channel(channel, config, http)
        return [c for c in found if c.published_at and first <= c.published_at <= last]

    return _collect(config, [*config.history_channels, *config.channels], fetch)


def _collect(config: SourceConfig, channels: list[Channel], fetch) -> DiscoveryReport:
    report = DiscoveryReport()
    seen: set[str] = set()
    for channel in channels:
        label = f"{channel.type}:{channel.url or channel.query}"
        try:
            found = fetch(channel)
        except HttpFailure as error:
            report.errors[label] = str(error)
            report.per_channel[label] = 0
            continue
        accepted = [c for c in found if accept_url(c.url, config)]
        report.per_channel[label] = len(accepted)
        for candidate in accepted:
            key = canonical_url(candidate.url, config)
            if key not in seen:
                seen.add(key)
                report.candidates.append(candidate)
    return report


def accept_url(url: str, config: SourceConfig) -> bool:
    if urlsplit(url).netloc.lower() not in config.domains:
        return False
    return not any(pattern in url for pattern in config.exclude_url_patterns)


def _fetch_channel(channel: Channel, config: SourceConfig, http: Http) -> list[Candidate]:
    if channel.type == "rss":
        url = channel.url
    elif channel.type == "bing_news":
        url = BING_NEWS_RSS.format(query=quote_plus(channel.query))
    else:
        raise ValueError(f"unknown channel type {channel.type!r}")
    response = http.get(url)
    if response.status_code >= 400:
        raise HttpFailure(f"GET {url} returned HTTP {response.status_code}")
    return parse_rss(response.text, config.source_id, channel.type)


def _archived_feed(channel: Channel, config: SourceConfig, http: Http, start: date, end: date) -> list[Candidate]:
    response = http.get(WAYBACK_CDX, params={"url": channel.url, "from": f"{start:%Y%m%d}",
                                             "to": f"{end + timedelta(days=2):%Y%m%d}",
                                             "filter": "statuscode:200", "fl": "timestamp",
                                             "collapse": "timestamp:8"})
    if response.status_code != 200:
        raise HttpFailure(f"archive index for {channel.url} returned HTTP {response.status_code}")
    candidates = []
    for timestamp in response.text.split():
        snapshot = http.get(WAYBACK_RAW.format(timestamp=timestamp, url=channel.url))
        if snapshot.status_code == 200:
            candidates += parse_rss(snapshot.text, config.source_id, "wayback_feed")
    return candidates


def parse_rss(xml: str, source_id: str, channel: str) -> list[Candidate]:
    soup = BeautifulSoup(xml, "xml")
    candidates = []
    for item in soup.find_all("item"):
        link = _text(item.find("link"))
        if not link:
            continue
        url = unwrap_redirect(link)
        candidates.append(Candidate(
            source_id=source_id,
            url=url,
            title=normalize_text(_text(item.find("title"))),
            summary=normalize_text(_strip_html(_text(item.find("description")))),
            published_at=_parse_rss_date(_text(item.find("pubDate"))),
            channel=channel,
        ))
    return candidates


def unwrap_redirect(link: str) -> str:
    parts = urlsplit(link)
    if parts.netloc.endswith("bing.com") and "url" in parse_qs(parts.query):
        return parse_qs(parts.query)["url"][0]
    return link


def _text(tag) -> str:
    return tag.get_text().strip() if tag else ""


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text)


def _parse_rss_date(value: str):
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(UTC)
    except (TypeError, ValueError):
        return None
