from datetime import UTC, datetime

import pytest

from mqc.extract import ExtractionError, canonical_url, extract_article, normalize_text
from mqc.models import BlockType, Candidate
from tests.conftest import FIXTURES


def _bbc_candidate():
    return Candidate(source_id="bbc", url="https://www.bbc.co.uk/news/articles/cm879dqn3e1jo?at_medium=RSS",
                     title="Feed title", channel="rss")


def test_bbc_article_body_excludes_captions_promos_and_video_notices(sources):
    html = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")
    article = extract_article(html, _bbc_candidate(), sources["bbc"], "u", "direct")

    texts = [b.text for b in article.blocks]
    assert len(texts) == 17
    assert texts[0] == "The Crimean Bridge has been attacked by Ukraine for the third time this week, Russia has said."
    assert not any("caption" in t.lower() or "video" in t.lower() for t in texts)
    assert not any(t.startswith("Putin's Crimean Bridge partly collapses") for t in texts)
    assert all(b.type == BlockType.PARAGRAPH for b in article.blocks)


def test_bbc_metadata_comes_from_json_ld(sources):
    html = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")
    article = extract_article(html, _bbc_candidate(), sources["bbc"], "u", "direct")

    assert article.title == "Putin's Crimean Bridge comes under attack again, Russian official says"
    assert article.published_at == datetime(2026, 11, 19, 1, 5, 3, 301000, tzinfo=UTC)
    assert article.published_at_source == "json_ld"
    assert article.url == "https://www.bbc.co.uk/news/articles/cm879dqn3e1jo"


def test_sentence_offsets_slice_back_to_the_sentence(sources):
    html = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")
    article = extract_article(html, _bbc_candidate(), sources["bbc"], "u", "direct")

    for block in article.blocks:
        rebuilt = " ".join(block.text[s.start:s.end] for s in block.sentences)
        assert rebuilt == block.text


def test_feed_date_is_used_when_page_has_none(sources):
    html = "<html><body><article><p>Kyiv said the talks would resume.</p></article></body></html>"
    candidate = Candidate(source_id="bbc", url="https://www.bbc.co.uk/news/articles/a", title="T",
                          published_at=datetime(2026, 11, 1, 8, tzinfo=UTC), channel="rss")

    article = extract_article(html, candidate, sources["bbc"].model_copy(update={"body_selector": None}), "u", "direct")

    assert article.published_at_source == "feed"


def test_url_date_is_last_resort_for_dates(sources):
    html = "<html><body><article><p>Kyiv said the talks would resume.</p></article></body></html>"
    candidate = Candidate(source_id="reuters", url="https://www.reuters.com/world/europe/talks-resume-2026-11-16/",
                          title="T", channel="bing_news")

    article = extract_article(html, candidate, sources["reuters"].model_copy(update={"body_selector": None}), "u", "wayback")

    assert article.published_at == datetime(2026, 11, 16, tzinfo=UTC)
    assert article.published_at_source == "url"


def test_page_without_body_raises(sources):
    with pytest.raises(ExtractionError):
        extract_article("<html><body><nav><p>Menu</p></nav></body></html>", _bbc_candidate(), sources["bbc"], "u", "direct")


def test_zero_width_characters_are_removed():
    assert normalize_text("Ukraine struck the \u200cbridge\u200b  linking\n Russia") == "Ukraine struck the bridge linking Russia"


def test_reuters_hash_prefix_and_query_are_stripped(sources):
    url = "https://www.reuters.com/3fec1b0a85a0/world/china/trumps-meeting-2026-11-18/?utm=x#top"
    assert canonical_url(url, sources["reuters"]) == "https://www.reuters.com/world/china/trumps-meeting-2026-11-18/"
