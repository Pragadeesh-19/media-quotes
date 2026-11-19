from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from mqc.config import SourceConfig
from mqc.discovery import DiscoveryReport, discover, discover_period
from mqc.extract import canonical_url
from mqc.extract import ExtractionError, extract_article
from mqc.fetch import FetchRequest, Unreachable, fetch_article
from mqc.llm import LlmClient, LlmError, LlmUnavailable
from mqc.models import ArticleStatus, Candidate
from mqc.net import Http
from mqc.relevance import RelevanceFilter
from mqc.runlog import RunLog
from mqc.prompts import PROMPT_VERSION
from mqc.selection import select_quotes
from mqc.store import Store
from mqc.verify import Rejection, build_quote, unverified_speaker

FETCH_STATUSES = {ArticleStatus.DISCOVERED, ArticleStatus.FAILED, ArticleStatus.UNREACHABLE}
TOPIC_KEY_LOOKBACK = timedelta(days=14)


@dataclass
class Context:
    store: Store
    http: Http
    llm: LlmClient
    relevance: RelevanceFilter
    sources: dict[str, SourceConfig]
    log: RunLog
    stats: Counter = field(default_factory=Counter)
    llm_unavailable: str | None = None


def discover_all(ctx: Context, source_ids: list[str]) -> None:
    for source_id in source_ids:
        config = ctx.sources[source_id]
        ctx.store.upsert_source(config)
        _record_discovery(ctx, config, discover(config, ctx.http), prefilter=False)


def discover_backfill(ctx: Context, source_ids: list[str], start: date, end: date) -> None:
    for source_id in source_ids:
        config = ctx.sources[source_id]
        ctx.store.upsert_source(config)
        _record_discovery(ctx, config, discover_period(config, ctx.http, start, end), prefilter=True)


def _record_discovery(ctx: Context, config: SourceConfig, report: DiscoveryReport, prefilter: bool) -> None:
    source_id = config.source_id
    new = 0
    for candidate in report.candidates:
        if not ctx.store.add_candidate(candidate, config):
            continue
        new += 1
        if prefilter:
            verdict = ctx.relevance.prefilter(candidate.title, candidate.summary, candidate.url)
            if not verdict.relevant:
                ctx.store.skip_candidate(canonical_url(candidate.url, config), verdict)
                ctx.stats[f"{source_id}.prefiltered_out"] += 1
    ctx.stats[f"{source_id}.discovered_new"] += new
    ctx.log.event("info", "discovery", source_id=source_id, per_channel=report.per_channel,
                  errors=report.errors, candidates=len(report.candidates), new=new)
    for channel, count in report.per_channel.items():
        if count == 0:
            ctx.log.event("warning", "channel_empty", source_id=source_id, channel=channel,
                          error=report.errors.get(channel))


def process_queue(ctx: Context, source_ids: list[str]) -> None:
    for source_id in source_ids:
        for row in ctx.store.work_queue(source_id):
            try:
                process_article(ctx, row)
            except Exception as error:
                ctx.store.mark(row["article_id"], ArticleStatus.FAILED, f"{type(error).__name__}: {error}", failed=True)
                ctx.stats["failed"] += 1
                ctx.log.event("error", "article_failed", article_id=row["article_id"], url=row["url"],
                              error=f"{type(error).__name__}: {error}")


def process_article(ctx: Context, row) -> None:
    config = ctx.sources[row["source_id"]]
    article_id = row["article_id"]
    if ArticleStatus(row["status"]) in FETCH_STATUSES:
        if not _fetch_and_judge(ctx, row, config):
            return
    select_and_store(ctx, article_id, config, replace=False)


def reselect(ctx: Context, source_ids: list[str], start: date, end: date) -> None:
    """Run quote selection again for articles already processed in the period, replacing their quotes."""
    first = datetime.combine(start, time.min, tzinfo=UTC)
    last = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
    for source_id in source_ids:
        for article_id in ctx.store.processed_articles(first, last, source_id):
            if ctx.llm_unavailable:
                return
            select_and_store(ctx, article_id, ctx.sources[source_id], replace=True)


def select_and_store(ctx: Context, article_id: str, config: SourceConfig, replace: bool) -> None:
    if ctx.llm_unavailable:
        return
    article = ctx.store.load_article(article_id)
    keys = ctx.store.topic_keys(article.published_at - TOPIC_KEY_LOOKBACK, article.published_at + timedelta(days=1))
    try:
        selection = select_quotes(article, ctx.llm, keys)
    except LlmUnavailable as error:
        ctx.llm_unavailable = str(error)
        ctx.log.event("warning", "llm_unavailable", detail=str(error))
        return
    except LlmError as error:
        ctx.log.event("error", "selection_failed", article_id=article_id, error=str(error))
        if not replace:
            ctx.store.mark(article_id, ArticleStatus.RELEVANT, f"LLM: {error}", failed=True)
        return
    quotes, rejections, speakers_dropped = [], [], []
    for proposal in selection.proposals:
        speaker_problem = unverified_speaker(proposal, article)
        if speaker_problem:
            speakers_dropped.append(speaker_problem.detail)
            proposal = proposal.model_copy(update={"speaker": None})
        result = build_quote(proposal, article, config)
        (rejections if isinstance(result, Rejection) else quotes).append(result)
    if replace:
        saved = ctx.store.replace_quotes(article_id, quotes, ctx.log.run_id, PROMPT_VERSION)
    else:
        saved = ctx.store.save_quotes(quotes, ctx.log.run_id, PROMPT_VERSION)
    ctx.store.mark(article_id, ArticleStatus.DONE, f"{len(quotes)} quotes, {len(rejections)} rejected")
    ctx.stats["quotes_saved"] += saved
    ctx.stats["quotes_rejected"] += len(rejections)
    ctx.log.event("info", "quotes", article_id=article_id, model=getattr(ctx.llm, "last_model", None),
                  prompt_version=PROMPT_VERSION, replaced=replace, proposed=len(selection.proposals),
                  dropped_over_cap=selection.dropped_over_cap, saved=saved,
                  rejected=[f"{r.rule}: {r.detail}" for r in rejections], speakers_dropped=speakers_dropped,
                  invalid=selection.invalid)


def _fetch_and_judge(ctx: Context, row, config: SourceConfig) -> bool:
    article_id = row["article_id"]
    request = FetchRequest(url=row["url"], lead=row["summary"], partners=config.syndication_partners)
    result = fetch_article(request, config.fetch_methods, ctx.http)
    if isinstance(result, Unreachable):
        ctx.store.mark(article_id, ArticleStatus.UNREACHABLE, result.reason, failed=True)
        ctx.stats[f"{config.source_id}.unreachable"] += 1
        ctx.log.event("warning", "unreachable", article_id=article_id, url=row["url"], reason=result.reason)
        return False
    candidate = Candidate(source_id=row["source_id"], url=row["url"], title=row["title"], summary=row["summary"],
                          published_at=row["published_at"], channel=row["channel"])
    try:
        article = extract_article(result.html, candidate, config, result.retrieval_url, result.method,
                                  body_selector=result.body_selector)
    except ExtractionError as error:
        ctx.store.mark(article_id, ArticleStatus.FAILED, f"extraction: {error}", failed=True)
        ctx.log.event("error", "extraction_failed", article_id=article_id, url=row["url"], error=str(error))
        return False
    verdict = ctx.relevance.judge(article.title, row["summary"], article.url, article.body_text())
    ctx.store.save_article(article, verdict)
    ctx.stats[f"{config.source_id}.{'relevant' if verdict.relevant else 'irrelevant'}"] += 1
    ctx.log.event("info", "relevance", article_id=article_id, url=article.url, relevant=verdict.relevant,
                  score=verdict.score, reason=verdict.reason, matched=verdict.matched, method=result.method)
    return verdict.relevant
