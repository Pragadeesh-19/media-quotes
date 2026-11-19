import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from mqc.config import SourceConfig
from mqc.models import Article, Block, BlockType, Candidate
from mqc.segment import split_sentences

_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
_WHITESPACE = re.compile(r"\s+")
_BLOCK_TAGS = {"p": BlockType.PARAGRAPH, "h2": BlockType.HEADING, "h3": BlockType.HEADING,
               "li": BlockType.LIST_ITEM, "blockquote": BlockType.BLOCKQUOTE}
_EXCLUDED_ANCESTORS = {"figure", "figcaption", "aside", "nav", "footer", "header", "form"}
_BOILERPLATE = re.compile(
    r"^\(?(read more|watch:|listen:|sign up|follow us|image (source|caption)|getty images|advertisement$|"
    r"reporting by|writing by|editing by|additional reporting|our standards:|"
    r"this video can not be played|to play this video)",
    re.IGNORECASE,
)
_URL_DATE = re.compile(r"-(\d{4}-\d{2}-\d{2})/?$")
SYNDICATION = "syndication"


class ExtractionError(Exception):
    pass


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text.translate(_INVISIBLE))
    return _WHITESPACE.sub(" ", text).strip()


def canonical_url(url: str, config: SourceConfig | None = None) -> str:
    parts = urlsplit(url.strip())
    path = parts.path or "/"
    if config and config.url_path_strip:
        path = re.sub(config.url_path_strip, "", path)
    return urlunsplit(("https", parts.netloc.lower(), path, "", ""))


def article_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def extract_article(html: str, candidate: Candidate, config: SourceConfig, retrieval_url: str,
                    retrieval_method: str, body_selector: str | None = None) -> Article:
    soup = BeautifulSoup(html, "lxml")
    url = canonical_url(candidate.url, config)
    if retrieval_method == SYNDICATION:
        metadata, page = {}, BeautifulSoup("", "lxml")
    else:
        metadata, page = _json_ld_article(soup), soup
    title = _title(page, metadata, candidate)
    published_at, published_at_source = _published_at(page, metadata, candidate, url)
    blocks = _blocks(soup, body_selector or config.body_selector)
    if not blocks:
        raise ExtractionError("no body text found")
    return Article(
        article_id=article_id(url),
        source_id=config.source_id,
        url=url,
        title=title,
        published_at=published_at,
        published_at_source=published_at_source,
        retrieval_url=retrieval_url,
        retrieval_method=retrieval_method,
        blocks=blocks,
    )


def _json_ld_article(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        for node in _json_ld_nodes(data):
            node_type = node.get("@type", "")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(str(t).endswith("Article") for t in types):
                return node
    return {}


def _json_ld_nodes(data) -> list[dict]:
    if isinstance(data, list):
        return [n for item in data for n in _json_ld_nodes(item)]
    if isinstance(data, dict):
        return [data, *_json_ld_nodes(data.get("@graph", []))]
    return []


def _title(soup: BeautifulSoup, metadata: dict, candidate: Candidate) -> str:
    og_title = soup.find("meta", property="og:title")
    for value in (metadata.get("headline"), og_title.get("content") if og_title else None, candidate.title):
        if value and normalize_text(value):
            return normalize_text(value)
    raise ExtractionError("no title")


def _published_at(soup: BeautifulSoup, metadata: dict, candidate: Candidate, url: str) -> tuple[datetime, str]:
    meta = soup.find("meta", property="article:published_time")
    for value, source in ((metadata.get("datePublished"), "json_ld"),
                          (meta.get("content") if meta else None, "meta")):
        parsed = _parse_datetime(value)
        if parsed:
            return parsed, source
    if candidate.published_at:
        return candidate.published_at.astimezone(UTC), "feed"
    url_date = _URL_DATE.search(url)
    if url_date:
        return datetime.fromisoformat(url_date.group(1)).replace(tzinfo=UTC), "url"
    raise ExtractionError("no publication date")


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _blocks(soup: BeautifulSoup, body_selector: str | None) -> tuple[Block, ...]:
    containers = soup.select(body_selector) if body_selector else []
    if not containers:
        containers = [soup.find("article") or soup.find("main") or soup.body or soup]
    elements = [el for c in containers for el in _block_elements(c)]
    blocks: list[Block] = []
    for element in elements:
        text = normalize_text(element.get_text(" "))
        if len(text) < 2 or _BOILERPLATE.match(text):
            continue
        index = len(blocks)
        blocks.append(Block(index=index, type=_block_type(element), text=text,
                            sentences=split_sentences(text, index)))
    return tuple(blocks)


def _block_elements(container: Tag) -> list[Tag]:
    if container.name in _BLOCK_TAGS or container.get("data-testid", "").startswith("paragraph-"):
        return [container]
    found = []
    for element in container.find_all(list(_BLOCK_TAGS)):
        ancestors = _ancestors_within(element, container)
        if ancestors & _EXCLUDED_ANCESTORS or ancestors & _BLOCK_TAGS.keys():
            continue
        found.append(element)
    return found


def _ancestors_within(element: Tag, container: Tag) -> set[str]:
    names = set()
    for parent in element.parents:
        if parent is container:
            break
        names.add(parent.name)
    return names


def _block_type(element: Tag) -> BlockType:
    return _BLOCK_TAGS.get(element.name, BlockType.PARAGRAPH)
