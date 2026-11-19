from dataclasses import dataclass, field

from pydantic import ValidationError

from mqc.llm import LlmClient
from mqc.models import Article, SpanProposal
from mqc.prompts import SELECTION_SYSTEM

MAX_CHUNK_CHARS = 40_000
CHUNK_OVERLAP_BLOCKS = 2


@dataclass
class Selection:
    proposals: list[SpanProposal] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


def select_quotes(article: Article, llm: LlmClient, existing_topic_keys: list[str]) -> Selection:
    selection = Selection()
    seen: set[tuple[str, str]] = set()
    for blocks in chunk_blocks(article):
        response = llm.complete_json(SELECTION_SYSTEM, render_article(article, blocks, existing_topic_keys))
        for item in response.get("quotes", []):
            try:
                proposal = SpanProposal.model_validate(item)
            except ValidationError as error:
                selection.invalid.append(f"{item!r}: {error.errors()[0]['msg']}")
                continue
            if (proposal.start, proposal.end) not in seen:
                seen.add((proposal.start, proposal.end))
                selection.proposals.append(proposal)
    return selection


def chunk_blocks(article: Article) -> list[tuple]:
    chunks, current, size = [], [], 0
    for block in article.blocks:
        if current and size + len(block.text) > MAX_CHUNK_CHARS:
            chunks.append(tuple(current))
            current = current[-CHUNK_OVERLAP_BLOCKS:]
            size = sum(len(b.text) for b in current)
        current.append(block)
        size += len(block.text)
    if current:
        chunks.append(tuple(current))
    return chunks


def render_article(article: Article, blocks, existing_topic_keys: list[str]) -> str:
    lines = [f"Title: {article.title}", f"Published: {article.published_at:%Y-%m-%d}", "",
             "Existing topic keys: " + (", ".join(existing_topic_keys) or "none"), "", "Article:"]
    for block in blocks:
        for sentence in block.sentences:
            lines.append(f"[{sentence.sid}] {block.text[sentence.start:sentence.end]}")
        lines.append("")
    return "\n".join(lines)
