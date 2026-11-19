import html
import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_HYPHENATED_LINE_END = re.compile(r"(\w)-\n(\w)")


class PdfError(Exception):
    pass


def pdf_to_html(data: bytes) -> str:
    """Render a PDF's text as an <article> of paragraphs so it flows through the normal HTML extractor."""
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError) as error:
        raise PdfError(f"unreadable PDF: {error}") from error
    text = _HYPHENATED_LINE_END.sub(r"\1\2", text)
    paragraphs = [" ".join(p.split()) for p in _PARAGRAPH_BREAK.split(text)]
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs if p)
    return f"<html><body><article>{body}</article></body></html>"
