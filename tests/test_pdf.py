import httpx

from mqc.extract import extract_article
from mqc.fetch import FetchRequest, Fetched, Unreachable, fetch_article
from mqc.models import Candidate
from mqc.net import Http
from mqc.pdf import pdf_to_html

LINES = [
    "Russian forces intensified attacks near Pokrovsk.",
    "Analysts expect the offensive to con-",
    "tinue into December.",
    "",
    "The EU agreed a new sanctions package.",
]


def make_pdf(lines: list[str]) -> bytes:
    text_ops = ["BT", "/F1 11 Tf", "14 TL", "72 720 Td"]
    for line in lines:
        text_ops.append(f"({line}) Tj T*" if line else "T*")
    stream = "\n".join(text_ops + ["ET"]).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def test_pdf_text_becomes_paragraphs_with_hyphenation_repaired():
    html = pdf_to_html(make_pdf(LINES))
    assert "Analysts expect the offensive to continue into December." in html
    assert "<p>" in html


def test_pdf_article_flows_through_extraction(sources):
    def handler(request):
        return httpx.Response(200, content=make_pdf(LINES), headers={"content-type": "application/pdf"})

    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    url = "https://www.bbc.co.uk/news/articles/report.pdf"
    result = fetch_article(FetchRequest(url), ["direct"], http)
    assert isinstance(result, Fetched)

    candidate = Candidate(source_id="bbc", url=url, title="Report", channel="rss",
                          published_at="2026-11-18T09:00:00+00:00")
    config = sources["bbc"].model_copy(update={"body_selector": None})
    article = extract_article(result.html, candidate, config, result.retrieval_url, result.method)

    text = article.body_text()
    assert "Russian forces intensified attacks near Pokrovsk." in text
    assert "The EU agreed a new sanctions package." in text


def test_corrupt_pdf_is_unreachable_not_a_crash():
    def handler(request):
        return httpx.Response(200, content=b"%PDF-1.4 garbage", headers={"content-type": "application/pdf"})

    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    result = fetch_article(FetchRequest("https://www.bbc.co.uk/x.pdf"), ["direct"], http)
    assert isinstance(result, Unreachable) and "unreadable PDF" in result.reason


def test_unsupported_content_type_is_reported():
    def handler(request):
        return httpx.Response(200, content=b"\x00", headers={"content-type": "video/mp4"})

    http = Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    result = fetch_article(FetchRequest("https://www.bbc.co.uk/x.mp4"), ["direct"], http)
    assert result == Unreachable("direct: unsupported content type video/mp4")
