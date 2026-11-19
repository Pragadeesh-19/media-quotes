import re
from urllib.parse import quote_plus, urlsplit

from bs4 import BeautifulSoup

from mqc.discovery import BING_NEWS_RSS, parse_rss
from mqc.extract import normalize_text
from mqc.net import Http

PROBE_WORDS = 12
LEAD_BLOCKS = 2
MAX_CANDIDATES = 3
_DATELINE = re.compile(r"^[A-Z][A-Za-z0-9 .,'/-]*\(Reuters\)\s*-\s*")


def lead_probe(summary: str) -> str | None:
    text = _DATELINE.sub("", normalize_text(summary)).removesuffix("...").strip()
    words = text.split()
    return " ".join(words[:PROBE_WORDS]) if len(words) >= PROBE_WORDS else None


def find_partner_copy(summary: str, partners: dict[str, str | None], http: Http) -> tuple[str, str, str] | str:
    """Return (html, url, body_selector) of a partner page that republishes the Reuters story, or a reason.

    A page qualifies only if its canonical link is on the partner's own domain, it credits Reuters, and the
    Reuters lead from the publisher's feed appears verbatim in its first two body paragraphs.
    """
    probe = lead_probe(summary)
    if not probe:
        return "feed lead too short to match"
    sites = " OR ".join(f"site:{host}" for host in partners)
    response = http.get(BING_NEWS_RSS.format(query=quote_plus(f'"{probe}" ({sites})')))
    if response.status_code != 200:
        return f"partner search HTTP {response.status_code}"
    reasons = []
    for candidate in [c for c in parse_rss(response.text, "partner", "bing_news")
                      if urlsplit(c.url).netloc in partners][:MAX_CANDIDATES]:
        host = urlsplit(candidate.url).netloc
        page = http.get(candidate.url)
        if page.status_code != 200:
            reasons.append(f"{candidate.url}: HTTP {page.status_code}")
            continue
        reason = check_partner_page(page.text, host, partners[host], probe)
        if reason is None:
            return page.text, candidate.url, partners[host]
        reasons.append(f"{candidate.url}: {reason}")
    return "; ".join(reasons) or "no partner copy found"


def check_partner_page(html: str, host: str, body_selector: str | None, probe: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    canonical = soup.find("link", rel="canonical")
    if not canonical or urlsplit(canonical.get("href", "")).netloc != host:
        return "canonical link is not on the partner domain"
    container = soup.select_one(body_selector) if body_selector else soup.find("article")
    if container is None:
        return "no article body"
    paragraphs = [normalize_text(p.get_text(" ")) for p in container.find_all("p")]
    paragraphs = [p for p in paragraphs if p and p != "Advertisement"]
    opening = " ".join(paragraphs[:LEAD_BLOCKS])
    if "(Reuters)" not in opening:
        return "no Reuters credit at the start of the article"
    if probe not in opening:
        return "Reuters lead not found at the start of the article"
    return None
