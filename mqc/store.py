import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from mqc.config import SourceConfig
from mqc.extract import article_id as make_article_id, canonical_url
from mqc.models import Article, ArticleStatus, Block, BlockType, Candidate, Quote
from mqc.relevance import Verdict
from mqc.segment import split_sentences

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    publication_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
    article_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    published_at_source TEXT,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    retrieval_url TEXT,
    retrieval_method TEXT,
    relevance_score REAL,
    relevance_terms TEXT,
    filter_version TEXT,
    discovered_at TEXT NOT NULL,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_source_published ON articles(source_id, published_at);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE TABLE IF NOT EXISTS blocks (
    article_id TEXT NOT NULL REFERENCES articles(article_id) ON DELETE CASCADE,
    block_index INTEGER NOT NULL,
    block_type TEXT NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (article_id, block_index)
);
CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    article_id TEXT NOT NULL REFERENCES articles(article_id),
    text TEXT NOT NULL CHECK (length(text) > 0),
    start_sid TEXT NOT NULL,
    end_sid TEXT NOT NULL,
    kind TEXT NOT NULL,
    speaker TEXT,
    section TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    extracted_at TEXT NOT NULL,
    run_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quotes_article ON quotes(article_id);
CREATE INDEX IF NOT EXISTS idx_quotes_section_topic ON quotes(section, topic_key);
CREATE TABLE IF NOT EXISTS supersessions (
    old_quote_id TEXT NOT NULL REFERENCES quotes(quote_id),
    new_quote_id TEXT NOT NULL REFERENCES quotes(quote_id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (old_quote_id, new_quote_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    stats_json TEXT
);
"""

MAX_ATTEMPTS = 3
WORK_STATUSES = (ArticleStatus.DISCOVERED, ArticleStatus.FAILED, ArticleStatus.UNREACHABLE, ArticleStatus.RELEVANT)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = DELETE")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert_source(self, config: SourceConfig) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sources VALUES (?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "name = excluded.name, publication_name = excluded.publication_name",
                (config.source_id, config.name, config.publication_name))

    def add_candidate(self, candidate: Candidate, config: SourceConfig) -> bool:
        url = canonical_url(candidate.url, config)
        published = candidate.published_at.astimezone(UTC).isoformat() if candidate.published_at else None
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO articles (article_id, source_id, url, title, summary, published_at, "
                "published_at_source, channel, status, discovered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (make_article_id(url), candidate.source_id, url, candidate.title, candidate.summary, published,
                 "feed" if published else None, candidate.channel, ArticleStatus.DISCOVERED, now_iso()))
        return cursor.rowcount == 1

    def work_queue(self, source_id: str | None = None) -> list[sqlite3.Row]:
        placeholders = ",".join("?" * len(WORK_STATUSES))
        sql = f"SELECT * FROM articles WHERE status IN ({placeholders}) AND attempts < ?"
        params: list = [*WORK_STATUSES, MAX_ATTEMPTS]
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        return self.conn.execute(sql + " ORDER BY published_at, article_id", params).fetchall()

    def mark(self, article_id: str, status: ArticleStatus, reason: str | None = None, failed: bool = False) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET status = ?, reason = ?, attempts = attempts + ? WHERE article_id = ?",
                (status, reason, int(failed), article_id))

    def save_article(self, article: Article, verdict: Verdict) -> None:
        status = ArticleStatus.RELEVANT if verdict.relevant else ArticleStatus.IRRELEVANT
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET title = ?, published_at = ?, published_at_source = ?, status = ?, reason = ?, "
                "retrieval_url = ?, retrieval_method = ?, relevance_score = ?, relevance_terms = ?, "
                "filter_version = ?, fetched_at = ? WHERE article_id = ?",
                (article.title, article.published_at.isoformat(), article.published_at_source, status, verdict.reason,
                 article.retrieval_url, article.retrieval_method, verdict.score, json.dumps(verdict.matched),
                 verdict.filter_version, now_iso(), article.article_id))
            self.conn.execute("DELETE FROM blocks WHERE article_id = ?", (article.article_id,))
            self.conn.executemany(
                "INSERT INTO blocks VALUES (?, ?, ?, ?)",
                [(article.article_id, b.index, b.type, b.text) for b in article.blocks])

    def load_article(self, article_id: str) -> Article:
        row = self.conn.execute("SELECT * FROM articles WHERE article_id = ?", (article_id,)).fetchone()
        rows = self.conn.execute(
            "SELECT * FROM blocks WHERE article_id = ? ORDER BY block_index", (article_id,)).fetchall()
        blocks = tuple(Block(index=r["block_index"], type=BlockType(r["block_type"]), text=r["text"],
                             sentences=split_sentences(r["text"], r["block_index"])) for r in rows)
        return Article(article_id=row["article_id"], source_id=row["source_id"], url=row["url"], title=row["title"],
                       published_at=datetime.fromisoformat(row["published_at"]),
                       published_at_source=row["published_at_source"], retrieval_url=row["retrieval_url"],
                       retrieval_method=row["retrieval_method"], blocks=blocks)

    def save_quotes(self, quotes: list[Quote], run_id: str) -> int:
        extracted_at = now_iso()
        with self.conn:
            cursor = self.conn.executemany(
                "INSERT OR IGNORE INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(q.quote_id, q.article_id, q.text, q.start_sid, q.end_sid, q.kind, q.speaker, q.section,
                  q.topic_key, q.claim_type, extracted_at, run_id) for q in quotes])
        return cursor.rowcount

    def topic_keys(self, start: datetime, end: datetime) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT q.topic_key FROM quotes q JOIN articles a USING (article_id) "
            "WHERE a.published_at >= ? AND a.published_at < ? ORDER BY q.topic_key",
            (start.isoformat(), end.isoformat())).fetchall()
        return [r["topic_key"] for r in rows]

    def window_quotes(self, start: datetime, end: datetime) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT q.*, a.url, a.title, a.published_at, a.retrieval_method, s.source_id, s.name AS source_name, "
            "s.publication_name FROM quotes q JOIN articles a USING (article_id) JOIN sources s USING (source_id) "
            "WHERE a.published_at >= ? AND a.published_at < ? ORDER BY a.published_at DESC, q.quote_id",
            (start.isoformat(), end.isoformat())).fetchall()

    def add_supersession(self, old_quote_id: str, new_quote_id: str, reason: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO supersessions VALUES (?, ?, ?, ?)",
                              (old_quote_id, new_quote_id, reason, now_iso()))

    def superseded_ids(self, quote_ids: list[str]) -> dict[str, sqlite3.Row]:
        placeholders = ",".join("?" * len(quote_ids))
        rows = self.conn.execute(
            f"SELECT * FROM supersessions WHERE old_quote_id IN ({placeholders}) AND new_quote_id IN ({placeholders})",
            [*quote_ids, *quote_ids]).fetchall() if quote_ids else []
        return {r["old_quote_id"]: r for r in rows}

    def start_run(self, run_id: str, command: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO runs VALUES (?, ?, ?, NULL, 'running', NULL)", (run_id, command, now_iso()))

    def finish_run(self, run_id: str, status: str, stats: dict) -> None:
        with self.conn:
            self.conn.execute("UPDATE runs SET finished_at = ?, status = ?, stats_json = ? WHERE run_id = ?",
                              (now_iso(), status, json.dumps(stats, sort_keys=True), run_id))

    def status_counts(self) -> dict[str, dict[str, int]]:
        rows = self.conn.execute("SELECT source_id, status, COUNT(*) AS n FROM articles GROUP BY source_id, status")
        counts: dict[str, dict[str, int]] = {}
        for r in rows:
            counts.setdefault(r["source_id"], {})[r["status"]] = r["n"]
        return counts

    def export_json(self, path: Path) -> int:
        rows = self.conn.execute(
            "SELECT q.quote_id, q.text, q.kind, q.speaker, q.section, q.topic_key, q.claim_type, q.start_sid, "
            "q.end_sid, q.extracted_at, q.run_id, a.article_id, a.title AS article_title, a.url AS article_url, "
            "a.published_at, a.retrieval_method, a.retrieval_url, s.source_id, s.name AS source_name, "
            "s.publication_name, (SELECT new_quote_id FROM supersessions WHERE old_quote_id = q.quote_id "
            "ORDER BY new_quote_id LIMIT 1) AS superseded_by "
            "FROM quotes q JOIN articles a USING (article_id) JOIN sources s USING (source_id) ORDER BY q.quote_id"
        ).fetchall()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=1), encoding="utf-8")
        return len(rows)
