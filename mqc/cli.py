import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from mqc.config import ROOT, llm_settings, load_lexicon, load_sources
from mqc.digest import build_digest
from mqc.evaluate import render_report, score_labels, write_template
from mqc.llm import OpenAiCompatibleClient
from mqc.net import Http
from mqc.pipeline import Context, discover_all, discover_backfill, process_queue
from mqc.relevance import RelevanceFilter
from mqc.runlog import RunLog, new_run_id
from mqc.store import Store

DB_PATH = ROOT / "data" / "mqc.db"
EXPORT_PATH = ROOT / "data" / "quotes.json"
DIGEST_DIR = ROOT / "digests"
LOG_DIR = ROOT / "logs"
LLM_CACHE_DIR = ROOT / ".cache" / "llm"
LABEL_DIR = ROOT / "eval" / "labels"
EVAL_REPORT = ROOT / "eval" / "results.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mqc", description="Media Quotes Collection System")
    sub = parser.add_subparsers(dest="command", required=True)

    daily = sub.add_parser("daily", help="discover, collect quotes, write today's digest")
    daily.add_argument("--date", type=date.fromisoformat, default=datetime.now(UTC).date())
    daily.add_argument("--source", action="append", help="limit to a source id (repeatable)")
    daily.add_argument("--max-llm-calls", type=int)

    backfill = sub.add_parser("backfill", help="collect quotes for a past period")
    backfill.add_argument("--start", type=date.fromisoformat, required=True)
    backfill.add_argument("--end", type=date.fromisoformat, required=True)
    backfill.add_argument("--source", action="append", help="limit to a source id (repeatable)")
    backfill.add_argument("--max-llm-calls", type=int)

    digest = sub.add_parser("digest", help="rebuild the digest for one day from the database")
    digest.add_argument("--date", type=date.fromisoformat, default=datetime.now(UTC).date())
    digest.add_argument("--max-llm-calls", type=int)

    sub.add_parser("export", help="write data/quotes.json from the database")

    evaluation = sub.add_parser("eval", help="hand-labelled evaluation")
    eval_sub = evaluation.add_subparsers(dest="eval_command", required=True)
    template = eval_sub.add_parser("template", help="write a blank labelling file for one day")
    template.add_argument("--date", type=date.fromisoformat, required=True)
    eval_sub.add_parser("score", help="score the system against every labelled day")

    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


def _context(args, command: str) -> Context:
    sources = load_sources()
    store = Store(DB_PATH)
    run_id = new_run_id()
    store.start_run(run_id, command)
    http = Http()
    llm = OpenAiCompatibleClient(llm_settings(), Http(min_interval=4.0, max_retries=1), LLM_CACHE_DIR,
                                 getattr(args, "max_llm_calls", None))
    return Context(store=store, http=http, llm=llm, relevance=RelevanceFilter(load_lexicon()), sources=sources,
                   log=RunLog(run_id, LOG_DIR))


def _write_digest(ctx: Context, day: date) -> Path:
    names = [ctx.sources[s].publication_name for s in sorted(ctx.sources)]
    markdown = build_digest(ctx.store, ctx.llm, day, ctx.log, names)
    DIGEST_DIR.mkdir(parents=True, exist_ok=True)
    path = DIGEST_DIR / f"{day.isoformat()}.md"
    path.write_text(markdown, encoding="utf-8")
    ctx.log.event("info", "digest_written", path=str(path.relative_to(ROOT)))
    return path


def _finish(ctx: Context, status: str) -> None:
    ctx.stats["llm_calls"] = ctx.llm.calls
    ctx.stats["llm_cache_hits"] = ctx.llm.cache_hits
    ctx.stats["exported_quotes"] = ctx.store.export_json(EXPORT_PATH)
    ctx.log.event("info", "run_finished", status=status, stats=dict(ctx.stats),
                  article_status=ctx.store.status_counts())
    ctx.store.finish_run(ctx.log.run_id, status, dict(ctx.stats))
    ctx.log.close()
    ctx.store.close()


def _run(args, command: str, body) -> int:
    ctx = _context(args, command)
    status = "ok"
    try:
        body(ctx)
    except Exception as error:
        status = "error"
        ctx.log.event("error", "run_failed", error=f"{type(error).__name__}: {error}")
        raise
    finally:
        _finish(ctx, status)
        print(f"run {ctx.log.run_id} {status}: {dict(ctx.stats)}")
    return 0


def cmd_daily(args) -> int:
    def body(ctx: Context) -> None:
        source_ids = args.source or sorted(ctx.sources)
        discover_all(ctx, source_ids)
        process_queue(ctx, source_ids)
        _write_digest(ctx, args.date)
    return _run(args, "daily", body)


def cmd_backfill(args) -> int:
    if args.end < args.start:
        raise SystemExit("--end must not be before --start")

    def body(ctx: Context) -> None:
        source_ids = args.source or sorted(ctx.sources)
        ctx.log.event("info", "backfill_period", start=args.start.isoformat(), end=args.end.isoformat())
        discover_backfill(ctx, source_ids, args.start, args.end)
        process_queue(ctx, source_ids)
    return _run(args, "backfill", body)


def cmd_digest(args) -> int:
    return _run(args, "digest", lambda ctx: _write_digest(ctx, args.date))


def cmd_export(_args) -> int:
    store = Store(DB_PATH)
    count = store.export_json(EXPORT_PATH)
    store.close()
    print(f"exported {count} quotes to {EXPORT_PATH.relative_to(ROOT)}")
    return 0


def cmd_eval(args) -> int:
    store = Store(DB_PATH)
    try:
        if args.eval_command == "template":
            path = LABEL_DIR / f"{args.date.isoformat()}.json"
            if path.exists():
                raise SystemExit(f"{path.relative_to(ROOT)} already exists; it may hold your labels")
            print(f"wrote {write_template(store, args.date, path)} articles to {path.relative_to(ROOT)}")
        else:
            files = sorted(LABEL_DIR.glob("*.json"))
            report = render_report(score_labels(store, files), files)
            EVAL_REPORT.write_text(report, encoding="utf-8")
            print(report)
    finally:
        store.close()
    return 0


COMMANDS = {"daily": cmd_daily, "backfill": cmd_backfill, "digest": cmd_digest, "export": cmd_export, "eval": cmd_eval}

if __name__ == "__main__":
    sys.exit(main())
