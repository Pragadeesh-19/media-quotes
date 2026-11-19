import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from mqc.llm import LlmClient, LlmError, json_list
from mqc.models import ClaimType, Section
from mqc.prompts import SUMMARY_SYSTEM, SUPERSESSION_SYSTEM
from mqc.segment import split_sentences
from mqc.store import Store

WINDOW_DAYS = 14
SUPERSEDABLE = {ClaimType.STATE.value, ClaimType.EXPECTATION.value}
MAX_SUMMARY_SENTENCES = 2
MAX_SUMMARY_WORDS = 60


@dataclass(frozen=True)
class Edge:
    old: str
    new: str
    reason: str


def window(day: date) -> tuple[datetime, datetime]:
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=UTC)
    return end - timedelta(days=WINDOW_DAYS), end


def validate_edges(proposed: list[dict], group: list[dict]) -> tuple[list[Edge], list[str]]:
    by_id = {q["quote_id"]: q for q in group}
    accepted, rejected = [], []
    for item in proposed:
        old, new = by_id.get(item.get("old")), by_id.get(item.get("new"))
        reason = str(item.get("reason", "")).strip()
        if old is None or new is None:
            rejected.append(f"{item!r}: quote not in this topic group")
        elif new["published_at"] <= old["published_at"]:
            rejected.append(f"{item!r}: superseding quote is not later")
        elif old["claim_type"] not in SUPERSEDABLE:
            rejected.append(f"{item!r}: {old['claim_type']} claims are never superseded")
        elif not reason:
            rejected.append(f"{item!r}: no reason given")
        else:
            accepted.append(Edge(old["quote_id"], new["quote_id"], reason))
    return accepted, rejected


def resolve_supersessions(store: Store, llm: LlmClient, quotes: list[dict], log) -> None:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for quote in quotes:
        groups[(quote["section"], quote["topic_key"])].append(quote)
    for (section, topic_key), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        group.sort(key=lambda q: (q["published_at"], q["quote_id"]))
        try:
            response = llm.complete_json(SUPERSESSION_SYSTEM, _render_group(topic_key, group))
        except LlmError as error:
            log.event("warning", "supersession_skipped", topic_key=topic_key, error=str(error))
            continue
        accepted, rejected = validate_edges([e for e in json_list(response, "superseded") if isinstance(e, dict)], group)
        for edge in accepted:
            store.add_supersession(edge.old, edge.new, edge.reason)
        log.event("info", "supersession", topic_key=topic_key, accepted=len(accepted), rejected=rejected)


def summarize_section(llm: LlmClient, section: Section, quotes: list[dict], log) -> str | None:
    user = f"Section: {section.value}\n\n" + "\n\n".join(f"- ({q['source_name']}, {q['published_at'][:10]}) {q['text']}"
                                                       for q in quotes)
    try:
        response = llm.complete_json(SUMMARY_SYSTEM, user)
        summary = str(response.get("summary", "") if isinstance(response, dict) else "").strip()
    except LlmError as error:
        log.event("warning", "summary_failed", section=section.value, error=str(error))
        return None
    if not summary or len(split_sentences(summary, 0)) > MAX_SUMMARY_SENTENCES or len(summary.split()) > MAX_SUMMARY_WORDS:
        log.event("warning", "summary_rejected", section=section.value, summary=summary)
        return None
    return summary


def build_digest(store: Store, llm: LlmClient, day: date, log, sources: list[str]) -> str:
    start, end = window(day)
    quotes = [dict(r) for r in store.window_quotes(start, end)]
    resolve_supersessions(store, llm, quotes, log)
    superseded = store.superseded_ids([q["quote_id"] for q in quotes])
    current = [q for q in quotes if q["quote_id"] not in superseded]
    by_section: dict[str, list[dict]] = defaultdict(list)
    for quote in current:
        by_section[quote["section"]].append(quote)
    summaries = {s: summarize_section(llm, s, by_section[s.value], log) for s in Section if by_section[s.value]}
    return render_markdown(day, start, end, by_section, summaries, len(superseded), sources)


def render_markdown(day: date, start: datetime, end: datetime, by_section: dict[str, list[dict]],
                    summaries: dict[Section, str | None], superseded_count: int, sources: list[str]) -> str:
    last = end - timedelta(days=1)
    shown = sum(len(v) for v in by_section.values())
    lines = [
        f"# Russia's War on Ukraine: Material Quotes, {start:%d %B %Y} to {last:%d %B %Y}",
        "",
        f"Digest for {day:%d %B %Y}. Period: {start:%Y-%m-%d} to {last:%Y-%m-%d} (14 days, by publication date, UTC). "
        f"{shown} current quotes from {', '.join(sources)}; {superseded_count} older quotes hidden as superseded.",
        "",
        "Quotes are copied verbatim from the source articles. Section summaries are generated by an LLM.",
        "",
    ]
    for section in Section:
        quotes = by_section.get(section.value)
        if not quotes:
            continue
        lines += [f"## {section.value}", ""]
        summary = summaries.get(section)
        lines += [f"*LLM summary:* {summary}" if summary else "*LLM summary unavailable for this section.*", ""]
        for quote in quotes:
            lines += render_quote(quote)
    return "\n".join(lines).rstrip() + "\n"


def render_quote(quote: dict) -> list[str]:
    lines = []
    for i, paragraph in enumerate(quote["text"].split("\n\n")):
        if i:
            lines.append(">")
        lines.append(f"> {paragraph}")
    attribution = [f"Attributed to: {quote['speaker']}"] if quote["speaker"] else []
    via = [f"via {quote['retrieval_method']} copy"] if quote["retrieval_method"] not in ("direct", None) else []
    meta = [*attribution, f"Source: {quote['source_name']}", f"Publication: {quote['publication_name']}",
            f"[{quote['title']}](<{quote['url']}>)", f"{quote['published_at'][:10]}", *via]
    lines += [">", "> " + " · ".join(meta), ""]
    return lines


def _render_group(topic_key: str, group: list[dict]) -> str:
    items = [{"id": q["quote_id"], "published": q["published_at"], "claim_type": q["claim_type"], "text": q["text"]}
             for q in group]
    return f"Topic: {topic_key}\n\nQuotes, oldest first:\n{json.dumps(items, ensure_ascii=False, indent=1)}"
