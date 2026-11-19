import html
import json
import re
import shutil
from pathlib import Path

from mqc.store import Store

_LINK = re.compile(r"\[([^\]]+)\]\(&lt;(.+?)&gt;\)")
_ITALIC = re.compile(r"\*([^*]+)\*")

STYLE = """
:root { --bg: #fbfaf7; --fg: #1d1d1b; --muted: #6b6a66; --line: #e2dfd8; --accent: #1f4e8c; --quote: #f2f0ea; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #e9e7e2; --muted: #a09e98; --line: #34332f; --accent: #8db4e8; --quote: #22211f; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 Georgia, "Times New Roman", serif; }
main { max-width: 860px; margin: 0 auto; padding: 24px 16px 64px; }
nav { font: 14px system-ui, sans-serif; display: flex; gap: 16px; flex-wrap: wrap; border-bottom: 1px solid var(--line);
      padding-bottom: 12px; margin-bottom: 24px; }
a { color: var(--accent); }
h1 { font-size: 1.7rem; line-height: 1.25; }
h2 { font-size: 1.25rem; margin-top: 2.2rem; border-bottom: 1px solid var(--line); padding-bottom: 4px; }
blockquote { margin: 16px 0; padding: 12px 16px; background: var(--quote); border-left: 3px solid var(--accent); }
blockquote p { margin: 0 0 8px; }
blockquote p.meta { font: 13px/1.4 system-ui, sans-serif; color: var(--muted); margin: 8px 0 0; }
table { width: 100%; border-collapse: collapse; font: 13px/1.4 system-ui, sans-serif; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.scroll { overflow-x: auto; }
.muted { color: var(--muted); font: 14px system-ui, sans-serif; }
input { width: 100%; padding: 8px; font: 15px system-ui, sans-serif; margin: 8px 0 12px; background: var(--bg);
        color: var(--fg); border: 1px solid var(--line); }
"""


def render_digest_html(markdown: str) -> str:
    out: list[str] = []
    quote: list[str] = []

    def flush_quote() -> None:
        if not quote:
            return
        paragraphs = [p for p in "\n".join(quote).split("\n\n") if p.strip()]
        body = "".join(f'<p{" class=\"meta\"" if i == len(paragraphs) - 1 else ""}>{_inline(p)}</p>'
                       for i, p in enumerate(paragraphs))
        out.append(f"<blockquote>{body}</blockquote>")
        quote.clear()

    for line in markdown.splitlines():
        if line.startswith(">"):
            quote.append(line[2:] if line.startswith("> ") else "")
            continue
        flush_quote()
        if line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    flush_quote()
    return "\n".join(out)


def _inline(text: str) -> str:
    linked = _LINK.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', html.escape(text))
    return _ITALIC.sub(r"<em>\1</em>", linked)


def build_site(store: Store, root: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "digests").mkdir(parents=True)
    (out / "logs").mkdir()
    (out / "data").mkdir()
    digests = sorted((root / "digests").glob("*.md"), reverse=True)
    for path in digests:
        content = render_digest_html(path.read_text(encoding="utf-8"))
        _page(out / "digests" / f"{path.stem}.html", f"Digest {path.stem}", content, depth=1)
    for log in (root / "logs").glob("*.jsonl"):
        shutil.copy(log, out / "logs" / log.name)
    for name in ("mqc.db", "quotes.json"):
        if (root / "data" / name).exists():
            shutil.copy(root / "data" / name, out / "data" / name)
    latest = render_digest_html(digests[0].read_text(encoding="utf-8")) if digests else "<p>No digest yet.</p>"
    _page(out / "index.html", "Media Quotes: latest digest", latest, depth=0)
    _page(out / "archive.html", "Digest archive", _archive(digests), depth=0)
    _page(out / "runs.html", "Runs and coverage", _runs(store), depth=0)
    _page(out / "quotes.html", "Quote database", _quotes_page(), depth=0)


def _archive(digests: list[Path]) -> str:
    items = "".join(f'<li><a href="digests/{p.stem}.html">{p.stem}</a></li>' for p in digests)
    return f"<h1>Digest archive</h1><ul>{items or '<li>none</li>'}</ul>"


def _runs(store: Store) -> str:
    coverage_rows = "".join(
        f"<tr><td>{html.escape(source)}</td><td>{html.escape(json.dumps(counts, sort_keys=True))}</td></tr>"
        for source, counts in sorted(store.status_counts().items()))
    runs = store.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 200").fetchall()
    run_rows = "".join(
        f"<tr><td><a href=\"logs/{r['run_id']}.jsonl\">{r['run_id']}</a></td><td>{r['command']}</td>"
        f"<td>{r['status']}</td><td>{html.escape(r['stats_json'] or '')}</td></tr>" for r in runs)
    return ("<h1>Runs and coverage</h1><h2>Articles by source and status</h2>"
            f'<div class="scroll"><table><tr><th>Source</th><th>Status counts</th></tr>{coverage_rows}</table></div>'
            "<h2>Runs</h2><p class=\"muted\">Each run log is a JSON-lines file with every keep, drop and "
            "rejection decision.</p>"
            f'<div class="scroll"><table><tr><th>Run</th><th>Command</th><th>Status</th><th>Stats</th></tr>'
            f"{run_rows}</table></div>")


def _quotes_page() -> str:
    return """<h1>Quote database</h1>
<p class="muted">Download the <a href="data/mqc.db">SQLite database</a> or the <a href="data/quotes.json">JSON export</a>
(every quote with its full metadata, ready for an LLM context).</p>
<input id="filter" placeholder="Filter by text, source, section or speaker">
<div class="scroll"><table><thead><tr><th>Date</th><th>Source</th><th>Section</th><th>Quote</th></tr></thead>
<tbody id="rows"></tbody></table></div>
<script>
const rows = document.getElementById("rows");
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
let quotes = [];
function draw() {
  const term = document.getElementById("filter").value.toLowerCase();
  const shown = quotes.filter(q => !term || [q.text, q.source_name, q.section, q.speaker].join(" ").toLowerCase().includes(term));
  rows.innerHTML = shown.slice(0, 500).map(q => `<tr><td>${esc(q.published_at.slice(0, 10))}</td>
    <td>${esc(q.source_name)}</td><td>${esc(q.section)}${q.superseded_by ? " (superseded)" : ""}</td>
    <td>${esc(q.text)}<br><a href="${esc(q.article_url)}">${esc(q.article_title)}</a></td></tr>`).join("");
}
fetch("data/quotes.json").then(r => r.json()).then(data => {
  quotes = data.sort((a, b) => b.published_at.localeCompare(a.published_at)); draw();
});
document.getElementById("filter").addEventListener("input", draw);
</script>"""


def _page(path: Path, title: str, body: str, depth: int) -> None:
    prefix = "../" * depth
    nav = (f'<nav><a href="{prefix}index.html">Latest digest</a><a href="{prefix}archive.html">Archive</a>'
           f'<a href="{prefix}quotes.html">Quotes</a><a href="{prefix}runs.html">Runs and logs</a></nav>')
    path.write_text(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>{STYLE}</style></head>
<body><main>{nav}{body}</main></body></html>
""", encoding="utf-8")
