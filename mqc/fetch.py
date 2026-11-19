from dataclasses import dataclass

import httpx

from mqc.net import Http, HttpFailure
from mqc.pdf import PdfError, pdf_to_html
from mqc.syndication import find_partner_copy

WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"


@dataclass(frozen=True)
class Fetched:
    html: str
    retrieval_url: str
    method: str
    body_selector: str | None = None


@dataclass(frozen=True)
class Unreachable:
    reason: str


@dataclass(frozen=True)
class FetchRequest:
    url: str
    lead: str = ""
    partners: dict[str, str | None] | None = None


def fetch_article(request: FetchRequest, methods: list[str], http: Http) -> Fetched | Unreachable:
    attempts = []
    for method in methods:
        try:
            result = _FETCHERS[method](request, http)
        except HttpFailure as error:
            attempts.append(f"{method}: {error}")
            continue
        if isinstance(result, Fetched):
            return result
        attempts.append(f"{method}: {result.reason}")
    return Unreachable("; ".join(attempts))


def _direct(request: FetchRequest, http: Http) -> Fetched | Unreachable:
    response = http.get(request.url)
    if response.status_code != 200:
        return Unreachable(f"HTTP {response.status_code}")
    return _document(response, str(response.url), "direct")


def _wayback(request: FetchRequest, http: Http) -> Fetched | Unreachable:
    response = http.get(WAYBACK_CDX, params={"url": request.url, "filter": "statuscode:200", "fl": "timestamp",
                                             "limit": "-1"})
    if response.status_code != 200:
        return Unreachable(f"archive index HTTP {response.status_code}")
    timestamps = response.text.split()
    if not timestamps:
        return Unreachable("no successful archive snapshot")
    snapshot_url = WAYBACK_RAW.format(timestamp=timestamps[-1], url=request.url)
    snapshot = http.get(snapshot_url)
    if snapshot.status_code != 200:
        return Unreachable(f"archive snapshot HTTP {snapshot.status_code}")
    return _document(snapshot, snapshot_url, "wayback")


def _document(response: httpx.Response, retrieval_url: str, method: str) -> Fetched | Unreachable:
    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type or response.content[:5] == b"%PDF-":
        try:
            return Fetched(pdf_to_html(response.content), retrieval_url, method)
        except PdfError as error:
            return Unreachable(str(error))
    if "html" in content_type or not content_type:
        return Fetched(response.text, retrieval_url, method)
    return Unreachable(f"unsupported content type {content_type}")


def _syndication(request: FetchRequest, http: Http) -> Fetched | Unreachable:
    if not request.partners:
        return Unreachable("no syndication partners configured")
    result = find_partner_copy(request.lead, request.partners, http)
    if isinstance(result, str):
        return Unreachable(result)
    html, url, body_selector = result
    return Fetched(html, url, "syndication", body_selector)


_FETCHERS = {"direct": _direct, "wayback": _wayback, "syndication": _syndication}
