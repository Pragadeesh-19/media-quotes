import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from mqc.extract import normalize_text
from mqc.models import Article, ArticleStatus
from mqc.store import Store

SYSTEM_RELEVANT = {ArticleStatus.RELEVANT, ArticleStatus.DONE}


@dataclass(frozen=True)
class Score:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float | None:
        predicted = self.true_positive + self.false_positive
        return self.true_positive / predicted if predicted else None

    @property
    def recall(self) -> float | None:
        actual = self.true_positive + self.false_negative
        return self.true_positive / actual if actual else None


def write_template(store: Store, day: date, path: Path) -> int:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    rows = store.conn.execute(
        "SELECT url, title, source_id FROM articles WHERE published_at >= ? AND published_at < ? ORDER BY source_id, url",
        (start.isoformat(), (start + timedelta(days=1)).isoformat())).fetchall()
    template = {
        "date": day.isoformat(),
        "instructions": "Set relevant to true or false. For relevant articles, paste every material quote "
                        "(copied from the article) into material_quotes. Add any war article the list misses.",
        "articles": [{"url": r["url"], "title": r["title"], "source_id": r["source_id"], "relevant": None,
                      "material_quotes": []} for r in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def score_labels(store: Store, label_files: list[Path]) -> dict:
    articles = Score(0, 0, 0)
    quotes = Score(0, 0, 0)
    misses: list[str] = []
    for path in label_files:
        for label in json.loads(path.read_text(encoding="utf-8"))["articles"]:
            if label["relevant"] is None:
                continue
            row = store.conn.execute("SELECT * FROM articles WHERE url = ?", (label["url"],)).fetchone()
            predicted = row is not None and ArticleStatus(row["status"]) in SYSTEM_RELEVANT
            articles = _add(articles, predicted, label["relevant"])
            if label["relevant"] and not predicted:
                misses.append(f"article: {label['url']}")
            if label["relevant"] and row is not None and row["status"] == ArticleStatus.DONE:
                article = store.load_article(row["article_id"])
                quotes = _merge(quotes, _score_quotes(store, article, label["material_quotes"], misses))
    return {"articles": articles, "quotes": quotes, "misses": misses}


def sentence_ids_for(article: Article, text: str) -> set[str]:
    wanted = normalize_text(text)
    ids = set()
    for block in article.blocks:
        position = block.text.find(wanted[:60]) if wanted else -1
        if position < 0:
            continue
        end = position + len(wanted)
        ids |= {s.sid for s in block.sentences if s.start < end and s.end > position}
    return ids


def _score_quotes(store: Store, article: Article, gold_texts: list[str], misses: list[str]) -> Score:
    gold = [sentence_ids_for(article, t) for t in gold_texts]
    for text, ids in zip(gold_texts, gold):
        if not ids:
            misses.append(f"label not found in stored article {article.url}: {text[:60]!r}")
    gold = [g for g in gold if g]
    order = [s.sid for b in article.blocks for s in b.sentences]
    system = []
    for q in store.conn.execute("SELECT start_sid, end_sid FROM quotes WHERE article_id = ?", (article.article_id,)):
        first, last = order.index(q["start_sid"]), order.index(q["end_sid"])
        system.append(set(order[first:last + 1]))
    matched_system = sum(1 for s in system if any(s & g for g in gold))
    matched_gold = sum(1 for g in gold if any(s & g for s in system))
    for g in gold:
        if not any(s & g for s in system):
            misses.append(f"quote missed in {article.url}: {sorted(g)}")
    return Score(matched_gold, len(system) - matched_system, len(gold) - matched_gold)


def _add(score: Score, predicted: bool, actual: bool) -> Score:
    return Score(score.true_positive + (predicted and actual), score.false_positive + (predicted and not actual),
                 score.false_negative + (actual and not predicted))


def _merge(a: Score, b: Score) -> Score:
    return Score(a.true_positive + b.true_positive, a.false_positive + b.false_positive,
                 a.false_negative + b.false_negative)


def render_report(result: dict, label_files: list[Path]) -> str:
    def pct(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.0%}"

    lines = ["# Evaluation", "", f"Hand-labelled days: {', '.join(p.stem for p in label_files)}", "",
             "| Stage | Precision | Recall | TP | FP | FN |", "|---|---|---|---|---|---|"]
    for name, score in (("Article detection", result["articles"]), ("Quote selection", result["quotes"])):
        lines.append(f"| {name} | {pct(score.precision)} | {pct(score.recall)} | {score.true_positive} | "
                     f"{score.false_positive} | {score.false_negative} |")
    lines += ["", "Quote matching is at sentence level: a selected quote counts as correct when it shares at "
              "least one sentence with a hand-labelled quote.", "", "## Misses", ""]
    lines += [f"- {m}" for m in result["misses"]] or ["- none"]
    return "\n".join(lines) + "\n"
