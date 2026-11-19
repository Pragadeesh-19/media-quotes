import json
from datetime import UTC, date, datetime

import httpx
import pytest

from mqc.digest import build_digest, render_quote, validate_edges, window
from mqc.llm import LlmError, LlmUnavailable
from mqc.models import ArticleStatus
from mqc.net import Http
from mqc.pipeline import Context, discover_all, process_queue, reselect
from mqc.prompts import PROMPT_VERSION, SELECTION_SYSTEM, SUMMARY_SYSTEM, SUPERSESSION_SYSTEM
from mqc.relevance import RelevanceFilter
from mqc.runlog import RunLog
from mqc.store import Store
from tests.conftest import FIXTURES

BBC_URL = "https://www.bbc.co.uk/news/articles/cm879dqn3e1jo"


class FakeLlm:
    def __init__(self, selection=None, superseded=None, summary="Ukraine keeps striking Russian logistics."):
        self.selection = selection if selection is not None else {"quotes": []}
        self.superseded = superseded or {"superseded": []}
        self.summary = summary
        self.calls = 0
        self.cache_hits = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.calls += 1
        if system == SELECTION_SYSTEM:
            if isinstance(self.selection, Exception):
                raise self.selection
            return self.selection
        if system == SUPERSESSION_SYSTEM:
            return self.superseded
        if system == SUMMARY_SYSTEM:
            return {"summary": self.summary}
        raise AssertionError("unexpected prompt")


def _bbc_only(sources):
    return {"bbc": sources["bbc"].model_copy(update={"channels": sources["bbc"].channels[:1]})}


def _transport():
    rss = (FIXTURES / "bbc_europe_rss.xml").read_text(encoding="utf-8")
    article = (FIXTURES / "bbc_article.html").read_text(encoding="utf-8")

    def handler(request):
        url = str(request.url)
        if url.endswith("rss.xml"):
            return httpx.Response(200, text=rss)
        if url == BBC_URL:
            return httpx.Response(200, text=article, headers={"content-type": "text/html"})
        if "cdx/search" in url:
            return httpx.Response(200, text="")
        return httpx.Response(404)
    return handler


@pytest.fixture
def ctx(tmp_path, sources, lexicon):
    store = Store(tmp_path / "t.db")
    http = Http(client=httpx.Client(transport=httpx.MockTransport(_transport())), sleep=lambda s: None)
    context = Context(store=store, http=http, llm=FakeLlm(), relevance=RelevanceFilter(lexicon),
                      sources=_bbc_only(sources), log=RunLog("test-run", tmp_path / "logs"))
    yield context
    context.log.close()
    store.close()


SELECTION = {"quotes": [
    {"start": "b0.s0", "end": "b0.s0", "kind": "reported_speech", "speaker": None,
     "section": "War Intensity & Battlefield", "topic_key": "war-intensity/crimean-bridge/attacks",
     "claim_type": "event"},
    {"start": "b13.s0", "end": "b13.s0", "kind": "reported_speech", "speaker": "Dmitry Peskov",
     "section": "Conflicting Parties' Resources", "topic_key": "resources/crimean-bridge/repair",
     "claim_type": "expectation"},
    {"start": "b0.s0", "end": "b5.s0", "kind": "authorial", "speaker": None,
     "section": "War Intensity & Battlefield", "topic_key": "x", "claim_type": "event"},
    {"start": "b1.s0", "end": "b1.s0", "kind": "nonsense"},
]}


def test_daily_flow_stores_verified_quotes_and_logs_rejections(ctx):
    ctx.llm.selection = SELECTION
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])

    quotes = ctx.store.conn.execute("SELECT * FROM quotes ORDER BY start_sid").fetchall()
    assert [q["text"][:40] for q in quotes] == ["The Crimean Bridge has been attacked by ",
                                                "In response, Kremlin spokesman Dmitry Pe"]
    article = ctx.store.conn.execute("SELECT * FROM articles WHERE url = ?", (BBC_URL,)).fetchone()
    assert article["status"] == ArticleStatus.DONE
    assert article["reason"] == "2 quotes, 1 rejected"
    events = [json.loads(l) for l in ctx.log.path.read_text(encoding="utf-8").splitlines()]
    quote_event = next(e for e in events if e["event"] == "quotes")
    assert quote_event["rejected"] == ["V1: more than 5 sentences"]
    assert len(quote_event["invalid"]) == 1


def test_unverifiable_speaker_is_dropped_but_quote_kept(ctx):
    ctx.llm.selection = {"quotes": [{**SELECTION["quotes"][1], "speaker": "Vladimir Putin"}]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    quote = ctx.store.conn.execute("SELECT * FROM quotes").fetchone()
    assert quote["speaker"] is None
    assert quote["text"].startswith("In response, Kremlin spokesman Dmitry Peskov")
    events = [json.loads(l) for l in ctx.log.path.read_text(encoding="utf-8").splitlines()]
    assert next(e for e in events if e["event"] == "quotes")["speakers_dropped"] == [
        "speaker 'Vladimir Putin' not found in the quote or preceding text"]


def test_unfetchable_articles_become_unreachable_with_reason(ctx):
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    row = ctx.store.conn.execute(
        "SELECT * FROM articles WHERE status = 'unreachable' ORDER BY url LIMIT 1").fetchone()
    assert row["reason"].startswith("direct: HTTP 404")
    assert row["attempts"] == 1


def test_llm_failure_keeps_article_relevant_for_retry(ctx):
    ctx.llm.selection = LlmError("429 quota")
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    row = ctx.store.conn.execute("SELECT * FROM articles WHERE url = ?", (BBC_URL,)).fetchone()
    assert row["status"] == ArticleStatus.RELEVANT
    assert row["attempts"] == 1


def test_missing_key_or_quota_skips_selection_without_using_an_attempt(ctx):
    ctx.llm.selection = LlmUnavailable("MQC_LLM_API_KEY is not set")
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    row = ctx.store.conn.execute("SELECT * FROM articles WHERE url = ?", (BBC_URL,)).fetchone()
    assert row["status"] == ArticleStatus.RELEVANT
    assert row["attempts"] == 0
    assert ctx.llm_unavailable == "MQC_LLM_API_KEY is not set"


def test_rerun_does_not_duplicate_quotes(ctx):
    ctx.llm.selection = SELECTION
    for _ in range(2):
        discover_all(ctx, ["bbc"])
        process_queue(ctx, ["bbc"])
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 2


def test_digest_quotes_are_verbatim_and_superseded_quotes_hidden(ctx):
    ctx.llm.selection = SELECTION
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    stored = {r["start_sid"]: r for r in ctx.store.conn.execute("SELECT * FROM quotes")}

    markdown = build_digest(ctx.store, ctx.llm, date(2026, 11, 19), ctx.log, ["BBC News"])

    assert markdown.startswith("# Russia's War on Ukraine: Material Quotes, 06 November 2026 to 19 November 2026")
    for row in stored.values():
        assert f"> {row['text']}" in markdown
    assert "Attributed to: Dmitry Peskov" in markdown
    assert f"(<{BBC_URL}>)" in markdown
    assert "*LLM summary:* Ukraine keeps striking Russian logistics." in markdown


def test_window_is_fourteen_days_inclusive():
    start, end = window(date(2026, 11, 19))
    assert start == datetime(2026, 11, 6, tzinfo=UTC)
    assert end == datetime(2026, 11, 20, tzinfo=UTC)


def _q(qid, published, claim="state"):
    return {"quote_id": qid, "published_at": published, "claim_type": claim}


def test_later_state_supersedes_earlier_state():
    group = [_q("a", "2026-11-10"), _q("b", "2026-11-15")]
    accepted, rejected = validate_edges([{"old": "a", "new": "b", "reason": "town captured"}], group)
    assert [(e.old, e.new) for e in accepted] == [("a", "b")] and not rejected


def test_supersession_must_point_forward_in_time():
    group = [_q("a", "2026-11-10"), _q("b", "2026-11-15")]
    accepted, rejected = validate_edges([{"old": "b", "new": "a", "reason": "x"}], group)
    assert not accepted and "not later" in rejected[0]


def test_events_are_never_superseded():
    group = [_q("a", "2026-11-10", "event"), _q("b", "2026-11-15")]
    accepted, rejected = validate_edges([{"old": "a", "new": "b", "reason": "x"}], group)
    assert not accepted and "never superseded" in rejected[0]


def test_edge_to_quote_outside_group_is_rejected():
    accepted, rejected = validate_edges([{"old": "a", "new": "zzz", "reason": "x"}], [_q("a", "2026-11-10")])
    assert not accepted and "not in this topic group" in rejected[0]


def test_multi_paragraph_quote_renders_as_one_blockquote():
    quote = {"text": "First para.\n\nSecond para.", "speaker": None, "source_name": "BBC",
             "publication_name": "BBC News", "title": "T", "url": "https://x", "published_at": "2026-11-19T01:00:00",
             "retrieval_method": "direct"}
    assert render_quote(quote)[:3] == ["> First para.", ">", "> Second para."]


def test_superseded_quote_is_hidden_in_digest(ctx):
    ctx.llm.selection = {"quotes": [
        {"start": "b13.s0", "end": "b13.s0", "kind": "reported_speech", "speaker": "Dmitry Peskov",
         "section": "Conflicting Parties' Resources", "topic_key": "resources/bridge", "claim_type": "expectation"}]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    old_id = ctx.store.conn.execute("SELECT quote_id FROM quotes").fetchone()[0]
    ctx.store.conn.execute(
        "INSERT INTO articles (article_id, source_id, url, title, published_at, channel, status, discovered_at) "
        "VALUES ('later', 'bbc', 'https://www.bbc.co.uk/news/articles/later', 'Later', '2026-11-19T09:00:00+00:00',"
        " 'rss', 'done', '2026-11-19')")
    ctx.store.conn.execute(
        "INSERT INTO quotes (quote_id, article_id, text, start_sid, end_sid, kind, speaker, section, topic_key, "
        "claim_type, extracted_at, run_id) VALUES ('newq', 'later', 'The bridge will not be rebuilt this year, "
        "officials said.', 'b0.s0', 'b0.s0', 'authorial', NULL, 'Conflicting Parties'' Resources', "
        "'resources/bridge', 'state', '2026-11-19', 'r')")
    ctx.llm.superseded = {"superseded": [{"old": old_id, "new": "newq", "reason": "repair outlook revised"}]}

    markdown = build_digest(ctx.store, ctx.llm, date(2026, 11, 19), ctx.log, ["BBC News"])

    assert "will be rebuilt" not in markdown
    assert "will not be rebuilt this year" in markdown
    assert "1 older quotes hidden as superseded" in markdown


def _quote(start: str, topic: str = "war-intensity/crimean-bridge/attacks") -> dict:
    return {"start": start, "end": start, "kind": "authorial", "speaker": None,
            "section": "War Intensity & Battlefield", "topic_key": topic, "claim_type": "state"}


def test_selection_is_capped_at_five_quotes_per_article(ctx):
    ctx.llm.selection = {"quotes": [_quote(f"b{i}.s0") for i in (0, 1, 2, 3, 5, 6, 7)]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 5
    events = [json.loads(l) for l in ctx.log.path.read_text(encoding="utf-8").splitlines()]
    assert next(e for e in events if e["event"] == "quotes")["dropped_over_cap"] == 2


def test_reselect_replaces_quotes_and_drops_their_supersession_links(ctx):
    ctx.llm.selection = {"quotes": [_quote("b0.s0"), _quote("b1.s0")]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    old_ids = [r[0] for r in ctx.store.conn.execute("SELECT quote_id FROM quotes ORDER BY start_sid")]
    ctx.store.add_supersession(old_ids[0], old_ids[1], "test link")

    ctx.llm.selection = {"quotes": [_quote("b5.s0")]}
    reselect(ctx, ["bbc"], date(2026, 11, 19), date(2026, 11, 19))

    rows = ctx.store.conn.execute("SELECT start_sid, prompt_version FROM quotes").fetchall()
    assert [tuple(r) for r in rows] == [("b5.s0", PROMPT_VERSION)]
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM supersessions").fetchone()[0] == 0
    status = ctx.store.conn.execute("SELECT status FROM articles WHERE url = ?", (BBC_URL,)).fetchone()[0]
    assert status == ArticleStatus.DONE


def test_failed_reselect_keeps_the_existing_quotes(ctx):
    ctx.llm.selection = {"quotes": [_quote("b0.s0")]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    ctx.llm.selection = LlmError("bad JSON")
    reselect(ctx, ["bbc"], date(2026, 11, 19), date(2026, 11, 19))
    assert ctx.store.conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 1
    row = ctx.store.conn.execute("SELECT status, attempts FROM articles WHERE url = ?", (BBC_URL,)).fetchone()
    assert (row["status"], row["attempts"]) == (ArticleStatus.DONE, 0)


def test_reselect_outside_the_period_touches_nothing(ctx):
    ctx.llm.selection = {"quotes": [_quote("b0.s0")]}
    discover_all(ctx, ["bbc"])
    process_queue(ctx, ["bbc"])
    ctx.llm.selection = {"quotes": [_quote("b5.s0")]}
    reselect(ctx, ["bbc"], date(2026, 11, 1), date(2026, 11, 18))
    assert [r[0] for r in ctx.store.conn.execute("SELECT start_sid FROM quotes")] == ["b0.s0"]
