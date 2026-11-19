import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from mqc.config import SourceConfig
from mqc.models import Article, BlockType, Quote, SpanProposal

MAX_SENTENCES = 5
MAX_BLOCKS = 2
QUOTABLE_BLOCKS = {BlockType.PARAGRAPH, BlockType.BLOCKQUOTE, BlockType.LIST_ITEM}
SPEAKER_LOOKBACK_BLOCKS = 2
_CONTAMINATION = re.compile(
    r"(advertisement|read more|sign up|getty images|image source|image caption|"
    r"page \d+ of \d+|\|\s*\||click here|subscribe)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Rejection:
    rule: str
    detail: str


def quote_id(article_id: str, text: str) -> str:
    return hashlib.sha256(f"{article_id}\n{text}".encode("utf-8")).hexdigest()[:16]


def slice_text(article: Article, start_sid: str, end_sid: str) -> str:
    index = article.sentence_index()
    start_block, start_sentence = index[start_sid]
    end_block, end_sentence = index[end_sid]
    if start_block.index == end_block.index:
        return start_block.text[start_sentence.start:end_sentence.end]
    parts = [start_block.text[start_sentence.start:]]
    parts += [b.text for b in article.blocks[start_block.index + 1:end_block.index]]
    parts.append(end_block.text[:end_sentence.end])
    return "\n\n".join(parts)


def build_quote(proposal: SpanProposal, article: Article, config: SourceConfig) -> Quote | Rejection:
    for rule in (_v1_span, _v4_block_types, _v5_metadata, _v6_source):
        rejection = rule(proposal, article, config)
        if rejection:
            return rejection
    text = slice_text(article, proposal.start, proposal.end)
    for rule in (_v3_quotation_marks, _v4_contamination, _v6_speaker):
        rejection = rule(text, proposal, article)
        if rejection:
            return rejection
    return Quote(
        quote_id=quote_id(article.article_id, text),
        article_id=article.article_id,
        text=text,
        start_sid=proposal.start,
        end_sid=proposal.end,
        kind=proposal.kind,
        speaker=proposal.speaker or None,
        section=proposal.section,
        topic_key=normalize_topic_key(proposal.topic_key),
        claim_type=proposal.claim_type,
    )


def unverified_speaker(proposal: SpanProposal, article: Article) -> Rejection | None:
    if _v1_span(proposal, article, None):
        return None
    return _v6_speaker(slice_text(article, proposal.start, proposal.end), proposal, article)


def verify_stored(quote: Quote, article: Article) -> Rejection | None:
    index = article.sentence_index()
    if quote.start_sid not in index or quote.end_sid not in index:
        return Rejection("V1", "stored span ids no longer exist in the article")
    if slice_text(article, quote.start_sid, quote.end_sid) != quote.text:
        return Rejection("V2", "stored text differs from the article slice")
    return None


def normalize_topic_key(key: str) -> str:
    parts = [re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-") for p in key.split("/")]
    return "/".join(p for p in parts if p)


def _ordered_sids(article: Article) -> list[str]:
    return [s.sid for b in article.blocks for s in b.sentences]


def _v1_span(proposal: SpanProposal, article: Article, _config) -> Rejection | None:
    order = {sid: i for i, sid in enumerate(_ordered_sids(article))}
    if proposal.start not in order or proposal.end not in order:
        return Rejection("V1", f"unknown sentence id {proposal.start}..{proposal.end}")
    if order[proposal.end] < order[proposal.start]:
        return Rejection("V1", "end before start")
    if order[proposal.end] - order[proposal.start] + 1 > MAX_SENTENCES:
        return Rejection("V1", f"more than {MAX_SENTENCES} sentences")
    index = article.sentence_index()
    if index[proposal.end][0].index - index[proposal.start][0].index + 1 > MAX_BLOCKS:
        return Rejection("V1", f"spans more than {MAX_BLOCKS} adjacent blocks")
    return None


def _v4_block_types(proposal: SpanProposal, article: Article, _config) -> Rejection | None:
    index = article.sentence_index()
    for block in article.blocks[index[proposal.start][0].index:index[proposal.end][0].index + 1]:
        if block.type not in QUOTABLE_BLOCKS:
            return Rejection("V4", f"block {block.index} is a {block.type}")
    return None


def _v5_metadata(_proposal, article: Article, _config) -> Rejection | None:
    missing = [f for f in ("article_id", "source_id", "url", "title") if not getattr(article, f).strip()]
    if missing or article.published_at is None:
        return Rejection("V5", f"missing metadata: {missing or ['published_at']}")
    return None


def _v6_source(_proposal, article: Article, config: SourceConfig) -> Rejection | None:
    host = urlsplit(article.url).netloc
    if article.source_id != config.source_id or host not in config.domains:
        return Rejection("V6", f"url host {host} not allowed for source {config.source_id}")
    return None


def _v3_quotation_marks(text: str, proposal: SpanProposal, article: Article) -> Rejection | None:
    if text.count("“") != text.count("”") or text.count('"') % 2:
        return Rejection("V3", "unbalanced quotation marks")
    block, sentence = article.sentence_index()[proposal.start]
    before = block.text[:sentence.start]
    if before.count("“") > before.count("”") or before.count('"') % 2:
        return Rejection("V3", "span starts inside an open quotation")
    return None


def _v4_contamination(text: str, _proposal, _article) -> Rejection | None:
    match = _CONTAMINATION.search(text)
    return Rejection("V4", f"contamination: {match.group(0)!r}") if match else None


def _v6_speaker(text: str, proposal: SpanProposal, article: Article) -> Rejection | None:
    if not proposal.speaker:
        return None
    block, sentence = article.sentence_index()[proposal.start]
    context = [b.text for b in article.blocks[max(0, block.index - SPEAKER_LOOKBACK_BLOCKS):block.index]]
    context.append(block.text[:sentence.start])
    if proposal.speaker not in text and not any(proposal.speaker in c for c in context):
        return Rejection("V6", f"speaker {proposal.speaker!r} not found in the quote or preceding text")
    return None
