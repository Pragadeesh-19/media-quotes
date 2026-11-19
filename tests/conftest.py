from datetime import UTC, datetime
from pathlib import Path

import pytest

from mqc.config import load_lexicon, load_sources
from mqc.extract import article_id, split_sentences
from mqc.models import Article, Block, BlockType

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def sources():
    return load_sources()


@pytest.fixture(scope="session")
def lexicon():
    return load_lexicon()


def make_article(paragraphs: list[str | tuple[BlockType, str]], url: str = "https://www.bbc.co.uk/news/articles/x1",
                 source_id: str = "bbc", published: datetime = datetime(2026, 11, 18, 9, tzinfo=UTC)) -> Article:
    blocks = []
    for i, item in enumerate(paragraphs):
        block_type, text = item if isinstance(item, tuple) else (BlockType.PARAGRAPH, item)
        blocks.append(Block(index=i, type=block_type, text=text, sentences=split_sentences(text, i)))
    return Article(article_id=article_id(url), source_id=source_id, url=url, title="Test article",
                   published_at=published, published_at_source="json_ld", retrieval_url=url,
                   retrieval_method="direct", blocks=tuple(blocks))
