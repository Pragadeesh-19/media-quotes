# Decisions

Short ADRs for the choices a reviewer would ask about. The first draft (27 Java-era ADRs) was replaced on 2026-11-19 before any code existed. Its reasoning is folded in below.

## ADR-001 Python, not Java

- **Date:** 2026-11-19
- **Context:** The plan was first drafted in Java, the author's strongest language. The job post asks for "hands-on experience with coding (Python)" from an AI team that will ask about every algorithm.
- **Decision:** Python 3.12 with httpx, BeautifulSoup + lxml, sqlite3, pydantic, pyahocorasick, pysbd and pytest.
- **Consequence:** The design carries over unchanged. The process is lighter: no ports for everything, fewer ADRs.

## ADR-002 The LLM returns sentence pointers, never quote text

- **Date:** 2026-11-19
- **Context:** Brief 2.3 forbids frankenquotes and altered or hallucinated text, and requires deterministic verification.
- **Decision:** The model sees the article as numbered sentences (`b{block}.s{n}`) and returns span boundaries plus labels. Code slices the verbatim text. The verifier proves contiguity, exactness and metadata completeness.
- **Alternatives:** The model returns text and we fuzzy-match it back to the article (can quietly accept small edits).
- **Consequence:** The model cannot alter a quote. Output tokens per call are small.

## ADR-003 Deterministic relevance: weighted Aho-Corasick lexicon

- **Date:** 2026-11-19
- **Context:** Brief 2.2: detect relevant articles "deterministically (not probabilistically)".
- **Decision:** A versioned weighted lexicon compiled with pyahocorasick. Score = field-weighted hits plus body density. Thresholds favour recall, because the LLM stage absorbs false positives cheaply. Every verdict is stored with its matched terms.
- **Alternatives:** Embeddings or an LLM classifier (probabilistic, so ruled out by the brief).

## ADR-004 Reuters: news search for discovery, public archives for text, honest gaps

- **Status:** Superseded by ADR-011

- **Date:** 2026-11-19
- **Context:** Probe on 2026-11-18: Reuters pages and sitemaps return 401 even from a residential IP. Google News RSS lists Reuters articles. The Wayback availability API works.
- **Decision:** Discover Reuters articles through Google News RSS. Read bodies only from public archive snapshots. Store unreadable articles as `unreachable` with title, date and link, and report the coverage. No syndication copies, no bot-wall evasion.
- **Alternatives:** Syndication partners (a legally grey area and fiddly matching); headless-browser evasion (breaks the publisher's terms).
- **Consequence:** Reuters quote coverage may be partial. The README states the measured numbers.

## ADR-005 Obsolescence as validated supersession edges

- **Date:** 2026-11-19
- **Context:** Brief 2.4: hide quotes whose facts were superseded by newer facts.
- **Decision:** Each quote carries `topic_key` and `claim_type`. Within `(section, topic_key)` groups, the LLM proposes `old -> new` edges. Code accepts an edge only if `new` is later and `old` is a `state` or `expectation` claim. Edges are stored with their reason, so every hidden quote is explainable.

## ADR-006 Fixed section taxonomy

- **Date:** 2026-11-19
- **Decision:** Seven sections: War Intensity & Battlefield; Peace Initiatives & Diplomacy; International Support; Sanctions & Economic Pressure; Conflicting Parties' Resources; Escalation & Security Risks; Domestic Politics & Public Opinion. Model output outside the enum is rejected. Prompts render the list from the enum.

## ADR-007 Default LLM: Gemini free tier via an OpenAI-compatible endpoint (model setting amended by ADR-014)

- **Date:** 2026-11-19
- **Context:** No credits. One call per article, so long context helps. The scheduled job runs in GitHub Actions.
- **Decision:** One small httpx client for any OpenAI-compatible endpoint, configured by `MQC_LLM_BASE_URL`, `MQC_LLM_MODEL` and `MQC_LLM_API_KEY`. The default is Gemini Flash. Responses are cached on disk by request hash to save free-tier quota.

## ADR-008 Storage and hosting: SQLite + JSON committed to the repo, GitHub Actions + Pages

- **Date:** 2026-11-19
- **Context:** The brief asks for an auditable DB, LLM-friendly storage, a daily run and a dashboard.
- **Decision:** `data/mqc.db` (SQLite) and `data/quotes.json` (denormalised quotes) are committed by the daily Actions run. Git history is the audit trail. A static dashboard on GitHub Pages shows digests, run logs and download links.
- **Alternatives:** Release-asset storage (more workflow steps); Postgres or Neo4j (servers we don't need).

## ADR-009 Measured quality: hand-labelled evaluation

- **Date:** 2026-11-19
- **Context:** Brief 4: "all material articles ... all material quotes ... detected".
- **Decision:** 1 to 2 days of articles labelled by hand in `eval/`. `mqc eval` reports precision and recall for article detection and quote selection.
- **Consequence:** About half a day of manual labelling by the author. The README cites only these numbers.

## ADR-010 Known limits accepted for the time budget

- **Date:** 2026-11-19
- **Decision:** No PDF extraction (BBC and Reuters are HTML). No source auto-profiler: a new outlet is one JSON config file. No cross-outlet duplicate merging.

## ADR-011 Reuters text from verified licensed partner copies

- **Date:** 2026-11-19
- **Context:** Measured: Wayback had a readable copy of 1 of 13 Reuters articles, and 0 of 29 in the first live run. Archives alone would leave a mandatory source nearly empty. The client asked to see workarounds.
- **Decision:** The fetch chain is direct, then Wayback, then partner copy (AOL, Yahoo UK). A partner page is accepted only if its canonical link is on its own domain, it credits Reuters in its opening, and the 12-word Reuters lead from the publisher's feed appears verbatim in its first two paragraphs. Metadata (URL, headline, date) stays Reuters'. Quotes are marked "via syndication copy".
- **Alternatives:** Archives only (8% or less); feed snippets (usually truncated mid-sentence); bot-wall evasion (ruled out by I7).
- **Consequence:** 8 of 29 Reuters articles readable in the first run (28%). The rest are logged as unreachable with reasons. See `docs/ACCESS.md`.

## ADR-012 Zero-width characters are removed during extraction

- **Date:** 2026-11-19
- **Context:** Reuters text contains invisible U+200B/U+200C characters inside words (seen in feed and partner text). Left in, they break verbatim matching and make quotes contain invisible bytes.
- **Decision:** `normalize_text` removes U+200B, U+200C, U+200D, U+2060, U+FEFF and soft hyphens, applies NFC and collapses whitespace. Stored text is the visible text of the article. Quotes are verbatim slices of that stored text.

## ADR-013 Unverifiable speaker: keep the quote, drop the attribution

- **Date:** 2026-11-19
- **Context:** In the first live run, 4 of 7 rejected quotes were valid passages where the model wrote the speaker differently from the article (for example "Vladimir Putin" when the article says "Putin").
- **Decision:** If the speaker does not appear verbatim in the quote or the 2 preceding paragraphs, the quote is stored with `speaker = NULL` and the dropped name is logged. Quote text verification is unchanged.
- **Consequence:** No unverified attribution reaches the database, and valid quotes are not lost.

## ADR-014 Ordered model fallback

- **Date:** 2026-11-19
- **Context:** On 2026-11-19 every full Flash model answered 503 "high demand", while Flash-Lite models answered in 4 to 9 seconds. `gemini-2.5-flash` is closed to new users.
- **Decision:** `MQC_LLM_MODELS` is an ordered list (default `gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`). A model that fails on capacity (429/5xx/timeout) is skipped for the rest of the run. The model used for each article is in the run log, and the cache key includes the model.

## ADR-015 Backfill sources: archived BBC feeds; Reuters only as far back as news search reaches

- **Date:** 2026-11-19
- **Context:** Backfill from 2026-10-01 needs dated article lists. Measured: BBC topic pages ignore `?page=` (older items load through an undocumented API); BBC archive sitemaps have no titles or publication dates; the GDELT API answered 429 even at one request per 12 s and empty results otherwise; Wayback index queries on reuters.com time out (504).
- **Decision:** BBC history comes from Wayback snapshots of its RSS feeds (world feed captured on 49 of the 50 days since 2026-10-01). Before fetching, a candidate must contain a war term in its title, summary or URL; the rest are stored as irrelevant with that reason. Reuters history uses extra Bing News queries, which reach back only a few weeks; older Reuters coverage is a documented gap, and the daily run closes it going forward.
- **Alternatives:** Fetching every BBC article in the archive sitemaps (about 10,000 page loads); decoding BBC's topic API (fragile); GDELT (unavailable).
