from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Section(StrEnum):
    WAR_INTENSITY = "War Intensity & Battlefield"
    PEACE_INITIATIVES = "Peace Initiatives & Diplomacy"
    INTERNATIONAL_SUPPORT = "International Support"
    SANCTIONS = "Sanctions & Economic Pressure"
    RESOURCES = "Conflicting Parties' Resources"
    ESCALATION = "Escalation & Security Risks"
    DOMESTIC_POLITICS = "Domestic Politics & Public Opinion"


class QuoteKind(StrEnum):
    DIRECT_SPEECH = "direct_speech"
    REPORTED_SPEECH = "reported_speech"
    AUTHORIAL = "authorial"


class ClaimType(StrEnum):
    STATE = "state"
    EVENT = "event"
    EXPECTATION = "expectation"
    OPINION = "opinion"


class BlockType(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    BLOCKQUOTE = "blockquote"


class ArticleStatus(StrEnum):
    DISCOVERED = "discovered"
    UNREACHABLE = "unreachable"
    FAILED = "failed"
    IRRELEVANT = "irrelevant"
    RELEVANT = "relevant"
    DONE = "done"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Sentence(Frozen):
    sid: str
    start: int
    end: int


class Block(Frozen):
    index: int
    type: BlockType
    text: str
    sentences: tuple[Sentence, ...]


class Candidate(Frozen):
    source_id: str
    url: str
    title: str
    summary: str = ""
    published_at: datetime | None = None
    channel: str


class Article(Frozen):
    article_id: str
    source_id: str
    url: str
    title: str
    published_at: datetime
    published_at_source: str
    retrieval_url: str
    retrieval_method: str
    blocks: tuple[Block, ...]

    def sentence_index(self) -> dict[str, tuple[Block, Sentence]]:
        return {s.sid: (b, s) for b in self.blocks for s in b.sentences}

    def body_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


class SpanProposal(BaseModel):
    start: str
    end: str
    kind: QuoteKind
    speaker: str | None = None
    section: Section
    topic_key: str
    claim_type: ClaimType


class Quote(Frozen):
    quote_id: str
    article_id: str
    text: str
    start_sid: str
    end_sid: str
    kind: QuoteKind
    speaker: str | None
    section: Section
    topic_key: str
    claim_type: ClaimType
