import json
from datetime import UTC, date, datetime

from mqc.evaluate import Score, score_labels, sentence_ids_for, write_template
from mqc.models import ClaimType, QuoteKind, Section, SpanProposal
from mqc.relevance import Verdict
from mqc.store import Store
from mqc.verify import build_quote
from tests.conftest import make_article

ARTICLE = make_article(["Russian forces advanced near Pokrovsk. The town is under heavy pressure.",
                        "The EU agreed new sanctions. They target tankers."],
                       published=datetime(2026, 11, 18, 9, tzinfo=UTC))


def _store(tmp_path, sources, quote_spans):
    store = Store(tmp_path / "t.db")
    store.upsert_source(sources["bbc"])
    store.conn.execute("INSERT INTO articles (article_id, source_id, url, title, published_at, channel, status, "
                       "discovered_at) VALUES (?, 'bbc', ?, 'T', ?, 'rss', 'discovered', 'x')",
                       (ARTICLE.article_id, ARTICLE.url, ARTICLE.published_at.isoformat()))
    store.save_article(ARTICLE, Verdict(True, 1.0, "r", {}, "v"))
    quotes = [build_quote(SpanProposal(start=a, end=b, kind=QuoteKind.AUTHORIAL, section=Section.WAR_INTENSITY,
                                       topic_key="t", claim_type=ClaimType.STATE), ARTICLE, sources["bbc"])
              for a, b in quote_spans]
    store.save_quotes(quotes, "run")
    store.mark(ARTICLE.article_id, "done")
    return store


def _labels(tmp_path, articles):
    path = tmp_path / "2026-11-18.json"
    path.write_text(json.dumps({"date": "2026-11-18", "articles": articles}), encoding="utf-8")
    return [path]


def test_template_lists_articles_without_system_verdicts(tmp_path, sources):
    store = _store(tmp_path, sources, [])
    path = tmp_path / "labels" / "day.json"
    assert write_template(store, date(2026, 11, 18), path) == 1
    entry = json.loads(path.read_text(encoding="utf-8"))["articles"][0]
    assert entry["relevant"] is None and "status" not in entry and "score" not in entry


def test_quote_precision_and_recall_use_sentence_overlap(tmp_path, sources):
    store = _store(tmp_path, sources, [("b0.s0", "b0.s1"), ("b1.s1", "b1.s1")])
    files = _labels(tmp_path, [{"url": ARTICLE.url, "relevant": True,
                                "material_quotes": ["The town is under heavy pressure.", "The EU agreed new sanctions."]}])

    result = score_labels(store, files)

    assert result["articles"] == Score(1, 0, 0)
    assert result["quotes"] == Score(true_positive=1, false_positive=1, false_negative=1)


def test_missed_article_counts_as_false_negative(tmp_path, sources):
    store = _store(tmp_path, sources, [])
    files = _labels(tmp_path, [{"url": "https://www.bbc.co.uk/news/articles/never-seen", "relevant": True,
                                "material_quotes": []}])
    result = score_labels(store, files)
    assert result["articles"] == Score(0, 0, 1)
    assert result["misses"] == ["article: https://www.bbc.co.uk/news/articles/never-seen"]


def test_unlabelled_entries_are_ignored(tmp_path, sources):
    store = _store(tmp_path, sources, [])
    assert score_labels(store, _labels(tmp_path, [{"url": ARTICLE.url, "relevant": None}]))["articles"] == Score(0, 0, 0)


def test_label_text_maps_to_sentence_ids():
    assert sentence_ids_for(ARTICLE, "The town is under  heavy pressure.") == {"b0.s1"}
    assert sentence_ids_for(ARTICLE, "Not in the article") == set()


def test_unreadable_articles_are_reported_separately(tmp_path, sources):
    store = _store(tmp_path, sources, [])
    store.conn.execute("INSERT INTO articles (article_id, source_id, url, title, published_at, channel, status, "
                       "discovered_at) VALUES ('r1', 'bbc', 'https://www.bbc.co.uk/news/articles/blocked', 'T', "
                       "'2026-11-18T10:00:00+00:00', 'rss', 'unreachable', 'x')")
    files = _labels(tmp_path, [{"url": "https://www.bbc.co.uk/news/articles/blocked", "relevant": True,
                                "material_quotes": []},
                               {"url": ARTICLE.url, "relevant": True, "material_quotes": []}])
    result = score_labels(store, files)
    assert result["articles"] == Score(1, 0, 1)
    assert result["readable"] == Score(1, 0, 0)
    assert result["misses"] == ["article (could not be read): https://www.bbc.co.uk/news/articles/blocked"]
    assert result["labellers"] == {"2026-11-18": "by hand"}
