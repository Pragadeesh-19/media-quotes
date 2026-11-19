# Media Quotes Collection System

[![tests](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/ci.yml/badge.svg)](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/ci.yml)
[![daily](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/daily.yml/badge.svg)](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/daily.yml)

Every day this system finds the Reuters and BBC articles (plus any outlet you add) that matter for understanding the trends and expectations of Russia's war on Ukraine. It extracts the material quotes **verbatim**, stores them with verified metadata, and publishes a Markdown digest of the current, non-obsolete quotes for the past 14 days.

- **Dashboard:** https://pragadeesh-19.github.io/media-quotes/ (latest digest, archive, searchable quotes, run logs, database download)
- **Latest digest:** [`digests/`](digests/)
- **Database:** [`data/mqc.db`](data/mqc.db) (SQLite) and [`data/quotes.json`](data/quotes.json) (every quote with its full metadata, ready to drop into an LLM context)
- **Decisions:** [`docs/DECISIONS.md`](docs/DECISIONS.md) · **Source access, measured:** [`docs/ACCESS.md`](docs/ACCESS.md)

## The core idea: the LLM proposes, deterministic code disposes

The brief asks for two things that pull in opposite directions: an LLM that *reasons* about which quotes matter, and *deterministic* guarantees that no quote is altered, stitched together or attributed to the wrong source. The design separates them cleanly.

**The LLM never writes quote text.** It reads the article as numbered sentences (`[b3.s1] ...`) and returns sentence ids only. Code then copies the exact text from the stored article. A "frankenquote" or a paraphrased quote cannot be produced by construction, and the verifier only has to prove that a span is valid.

```
  STEP                                   WHO                    WHY
  Which URLs exist today per source      code                   brief 2.2 "deterministically"
  Which are about the war                code (Aho-Corasick)    deterministic, explainable, measurable
  Clean article structure                code                   site rules + JSON-LD metadata
  Which passages are material quotes     LLM -> sentence ids    needs reading comprehension
  Proposed quote is real and complete    code (verifier)        brief 2.3 "deterministic tools"
  Section of each quote                  LLM, fixed enum        judgement, output constrained
  Which quotes are obsolete              LLM proposes edges,    judgement; code checks dates and
                                         code validates         claim types, stores edge + reason
  Digest layout and quote text           code                   brief 2.4 "no change in the text"
  Section summary                        LLM, labelled          brief 2.4 "non-deterministic inference"
```

### Invariants

| ID | Invariant | Enforced by |
|----|-----------|-------------|
| I1 | The LLM never writes quote text. It returns sentence ids; code copies the text. | Selection schema has no text field |
| I2 | A stored quote is a byte-exact, contiguous slice of one stored article. | `verify.py` rules V1–V3 |
| I3 | Discovery and relevance filtering are deterministic: same input and lexicon version, same verdict. | `relevance.py`, `filter_version` stored |
| I4 | No quote is stored with missing metadata. | `NOT NULL`/`CHECK` constraints + rule V5 |
| I5 | The digest copies quote text and metadata unchanged. Only section summaries are LLM prose, and they are labelled. | `digest.py`; test compares rendered quotes to DB text |
| I6 | Every keep or drop decision on an article or quote is recorded with its reason. | Run logs, `articles` columns, `supersessions` table |
| I7 | No access-control circumvention (paywalls, CAPTCHAs, bot walls). Public channels only; what cannot be reached is logged and reported. | `fetch.py` chain |

## Requirement trace

| Brief | Requirement | Where |
|-------|-------------|-------|
| 2.1 | Database with quote ID, text, source ID/name, publication date, article name, link, extraction date | `mqc/store.py` (SQLite) |
| 2.1 | Storage efficient for LLM use | `data/quotes.json`: one denormalised object per quote |
| 2.2 | Check each source daily, detect relevant articles deterministically | `.github/workflows/daily.yml`, `mqc/discovery.py`, `mqc/relevance.py` |
| 2.2 | Read articles, reason about relevant quotes | `mqc/selection.py`, `mqc/prompts.py` |
| 2.3 | Extract quotes and metadata; verify deterministically | `mqc/extract.py`, `mqc/verify.py` |
| 2.3 | Keep the original formatting structure | Typed blocks (paragraph, list item, blockquote); multi-paragraph quotes keep their breaks |
| 2.3 | Quotes from 2026-10-01 to today | `mqc backfill --start 2026-10-01 --end <today>` (done) |
| 2.3 plus | Any past period on request | `mqc backfill --start 2026-08-01 --end 2026-09-30` |
| 2.3 plus | Name a new outlet and get a pipeline for it | `mqc add-source <homepage> --backfill-from 2026-10-01` (The Kyiv Independent was added this way) |
| 1 plus | Other authoritative sources | The Kyiv Independent |
| 2.4 | Daily MD, 14-day window in the title, sections, verbatim quotes with source, publication, article name and link, no obsolete quotes, 1–2 sentence LLM summary per section | `mqc/digest.py` → `digests/YYYY-MM-DD.md` |
| 3 | Auditable DB, dashboard (latest MDs, logs, DB), GitHub | SQLite + JSON in git, `mqc/site.py` → GitHub Pages |
| 4 | Recall and precision of detection and quote selection | `mqc eval` (hand-labelled evaluation) |
| 4 | Network errors, unavailable content | Retries with backoff and `Retry-After`, per-host rate limit, fetch fallback chain, per-article status that resumes after a crash |
| 4 | Any size or content type | Long articles split on paragraph boundaries; PDFs converted to paragraphs; other types logged as unsupported |
| 4 | Logs for traceability | `logs/<run_id>.jsonl`: every discovery, verdict, rejection and supersession |

## Pipeline

```
config/sources/*.json
  -> discovery    RSS feeds, Bing News (Reuters), archived feed snapshots (backfill)
  -> relevance    Aho-Corasick over title, summary, URL and body (deterministic, versioned lexicon)
  -> fetch        direct -> Wayback snapshot -> verified partner copy (Reuters) -> logged as unreachable
  -> extract      HTML or PDF -> typed blocks -> sentences b{block}.s{n}; metadata from JSON-LD
  -> select       LLM returns sentence-id spans + section, topic, claim type
  -> verify       rules V1..V6 -> SQLite
  -> digest       window [D-13, D] -> supersession -> Markdown -> LLM section summaries
  -> publish      data/quotes.json, digests/, logs/, dashboard
```

Each article moves through `discovered -> relevant | irrelevant -> done`, or `unreachable`/`failed` with an attempt count and reason. Every run picks up unfinished articles, so a crash, an exhausted LLM quota or a network outage loses nothing.

### Verification rules

| Rule | Checks |
|------|--------|
| V1 | Sentence ids exist; the span is contiguous, at most 5 sentences and 2 adjacent paragraphs |
| V2 | Stored text equals the article slice byte for byte (re-checkable at any time) |
| V3 | Quotation marks are balanced, and the span does not start inside someone else's quotation |
| V4 | Only quotable blocks (no headings); no ads, captions, "Read more", page numbers or table pipes |
| V5 | Title, link, outlet and publication date are present |
| V6 | The link is on the source's own domain; a named speaker must appear verbatim in the quote or the two paragraphs before it, otherwise the attribution is dropped and logged |

### Obsolete quotes

Each quote carries a `topic_key` and a `claim_type` (`state`, `event`, `expectation`, `opinion`). For quotes on the same topic in the 14-day window, the LLM proposes "old is superseded by new" pairs with a reason. Code accepts a pair only if the newer quote really is later and the older claim is a `state` or `expectation`: an event that happened is never superseded. Accepted pairs are stored with their reason, so every hidden quote can be explained.

## Sources and access

| Source | How articles are found | How text is read | Coverage |
|--------|------------------------|------------------|----------|
| BBC | RSS feeds; archived feed snapshots for backfill | Direct | Every candidate with a war term in its title, summary or URL is fetched; the rest are recorded as irrelevant with that reason |
| Reuters | Bing News RSS (carries the real reuters.com URL) | reuters.com returns 401 to automated clients and Wayback holds almost no readable copies, so text comes from licensed partner copies (AOL, Yahoo UK). A copy is accepted only if its canonical link is its own, it credits Reuters, and it opens with the Reuters lead from the feed. | Partial: see below |
| The Kyiv Independent | RSS feed found by `add-source` | Direct | Same as BBC |

Reuters quotes keep the reuters.com link, headline and date, and are marked "via syndication copy" in the digest. Reuters articles that cannot be read are stored with title, date and link, retried, and counted on the dashboard. The measurements behind these choices are in [`docs/ACCESS.md`](docs/ACCESS.md).

## Data model

| Table | Holds |
|-------|-------|
| `sources` | Outlets |
| `articles` | One row per canonical URL: title, publication date and where it came from, status and reason, relevance score and matched terms, retrieval URL and method |
| `blocks` | The cleaned article body, paragraph by paragraph (what quotes are verified against) |
| `quotes` | Quote text, sentence span, kind, speaker, section, topic, claim type, extraction time, run id |
| `supersessions` | Obsolete-quote pairs with the reason |
| `runs` | Every command run with its statistics |

`quote_id` is a hash of the article and the exact quote text, and `article_id` a hash of the canonical URL, so re-runs never duplicate rows. All times are UTC.

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate          # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"
export MQC_LLM_API_KEY=...                              # Gemini API key (free tier works)

mqc daily                                               # discover, collect, write today's digest
mqc backfill --start 2026-08-01 --end 2026-09-30        # collect a past period
mqc add-source https://kyivindependent.com/ --backfill-from 2026-10-01
mqc digest --date 2026-11-19                            # rebuild one digest from the database
mqc site                                                # build the dashboard into site/
mqc eval template --date 2026-11-18                     # blank labelling file for one day
mqc eval score                                          # precision and recall against the labels
pytest                                                  # 93 tests, no network needed
```

The LLM is any OpenAI-compatible endpoint. `MQC_LLM_MODELS` is an ordered fallback list (default `gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`); an overloaded model is skipped for the rest of the run, and responses are cached by request hash.

### Automation

`.github/workflows/daily.yml` runs every day at 06:00 UTC: it collects, writes the digest, commits `data/`, `digests/` and `logs/`, and publishes the dashboard. Run it by hand with `start`, `end` and optionally `source` to backfill a period.

## Quality

- **Tests:** 93 tests run on every push without network access. They cover extraction against saved real BBC, AOL and Bing pages, every verification rule (including frankenquote attempts), the fetch fallback chain under 401, 429 and timeouts, supersession rules, the digest's verbatim rendering, backfill, source onboarding and PDF handling.
- **Evaluation:** `mqc eval template` writes a blind labelling file for a day (the system's decisions are not shown). `mqc eval score` reports precision and recall for article detection and, at sentence level, for quote selection. Results go to `eval/results.md`. No hand-labelled days are committed yet, so this repository makes no recall or precision claim.

## Known limits

- **Reuters coverage is partial.** It depends on licensed partner copies being findable, and older Reuters articles only go back as far as Bing's news index. Unreadable Reuters articles are listed, not hidden.
- **Free-tier LLM capacity.** Gemini's free tier often answers 503 or 429. The pipeline stops LLM work cleanly and the next run continues the queue, so collection catches up over days rather than in one run. The quote volume per article reflects the lighter fallback models used under that load.
- **Outlets that block automated clients** (for example ISW returned 403) cannot be added; `add-source` says so instead of guessing.
- **Paywalled outlets** (FT, Bloomberg, The Economist) are out of reach by design (I7).
