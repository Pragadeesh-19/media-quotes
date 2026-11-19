from mqc.site import build_site, render_digest_html
from mqc.store import Store

DIGEST = """# Russia's War on Ukraine: Material Quotes, 06 November 2026 to 19 November 2026

## War Intensity & Battlefield

*LLM summary:* Attacks are intensifying.

> First paragraph with <b>markup</b> & "marks".
>
> Second paragraph.
>
> Source: BBC · [A & B](<https://www.bbc.co.uk/news/articles/x?a=1&b=2>) · 2026-11-19
"""


def test_digest_quote_paragraphs_and_metadata_render():
    page = render_digest_html(DIGEST)
    assert "<h2>War Intensity &amp; Battlefield</h2>" in page
    assert "<p><em>LLM summary:</em> Attacks are intensifying.</p>" in page
    assert "<p>First paragraph with &lt;b&gt;markup&lt;/b&gt; &amp; &quot;marks&quot;.</p><p>Second paragraph.</p>" in page
    assert '<p class="meta">Source: BBC · <a href="https://www.bbc.co.uk/news/articles/x?a=1&amp;b=2">A &amp; B</a>' in page


def test_site_contains_pages_logs_and_downloads(tmp_path):
    root = tmp_path / "repo"
    (root / "digests").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "data").mkdir()
    (root / "digests" / "2026-11-19.md").write_text(DIGEST, encoding="utf-8")
    (root / "logs" / "run1.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "data" / "quotes.json").write_text("[]", encoding="utf-8")
    store = Store(root / "data" / "mqc.db")

    build_site(store, root, root / "site")
    store.close()

    site = root / "site"
    for page in ("index.html", "archive.html", "runs.html", "quotes.html", "digests/2026-11-19.html",
                 "logs/run1.jsonl", "data/quotes.json", "data/mqc.db"):
        assert (site / page).exists(), page
    assert "Attacks are intensifying." in (site / "index.html").read_text(encoding="utf-8")
