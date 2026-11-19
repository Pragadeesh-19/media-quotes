from datetime import UTC, date, datetime

import httpx

from mqc.config import Channel
from mqc.discovery import DiscoveryReport, discover_period
from mqc.models import ArticleStatus, Candidate
from mqc.net import Http
from mqc.pipeline import Context, _record_discovery
from mqc.relevance import RelevanceFilter
from mqc.runlog import RunLog
from mqc.store import Store
from tests.conftest import FIXTURES

FEED = "https://feeds.bbci.co.uk/news/world/europe/rss.xml"


def _archive_only(sources):
    return sources["bbc"].model_copy(update={"channels": [], "history_channels": [Channel(type="wayback_feed", url=FEED)]})


def _http(requested: list[str]):
    rss = (FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8")

    def handler(request):
        url = str(request.url)
        requested.append(url)
        if "cdx/search" in url:
            return httpx.Response(200, text="20261118090000\n20261119090000\n")
        if "id_/" in url:
            return httpx.Response(200, text=rss)
        return httpx.Response(404)
    return Http(client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)


def test_archived_feed_snapshots_are_read_and_filtered_to_the_period(sources):
    requested: list[str] = []
    report = discover_period(_archive_only(sources), _http(requested), date(2026, 11, 18), date(2026, 11, 18))

    assert report.candidates
    assert all(c.published_at.date() == date(2026, 11, 18) for c in report.candidates)
    assert len({c.url for c in report.candidates}) == len(report.candidates)
    assert sum("id_/" in u for u in requested) == 2


def test_prefilter_keeps_war_terms_and_drops_the_rest(lexicon):
    f = RelevanceFilter(lexicon)
    assert f.prefilter("Trump shakes hands with Putin and Xi", "", "").relevant
    assert not f.prefilter("Van Gogh painting sells for record", "Auction in New York", "").relevant


def test_prefiltered_candidates_are_stored_as_irrelevant_with_reason(tmp_path, sources, lexicon):
    store = Store(tmp_path / "t.db")
    ctx = Context(store=store, http=None, llm=None, relevance=RelevanceFilter(lexicon), sources=sources,
                  log=RunLog("t", tmp_path / "logs"))
    store.upsert_source(sources["bbc"])
    published = datetime(2026, 10, 2, tzinfo=UTC)
    report = DiscoveryReport(candidates=[
        Candidate(source_id="bbc", url="https://www.bbc.co.uk/news/articles/a1", title="Kyiv hit by drones",
                  published_at=published, channel="wayback_feed"),
        Candidate(source_id="bbc", url="https://www.bbc.co.uk/news/articles/a2", title="Hippo swallows hat",
                  published_at=published, channel="wayback_feed"),
    ])

    _record_discovery(ctx, sources["bbc"], report, prefilter=True)

    rows = {r["url"][-2:]: r for r in store.conn.execute("SELECT * FROM articles")}
    assert rows["a1"]["status"] == ArticleStatus.DISCOVERED
    assert rows["a2"]["status"] == ArticleStatus.IRRELEVANT
    assert rows["a2"]["reason"] == "prefilter: no war term in title, summary or url"
    assert [r["url"][-2:] for r in store.work_queue("bbc")] == ["a1"]
    ctx.log.close()
    store.close()
