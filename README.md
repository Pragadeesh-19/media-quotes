# Media Quotes Collection System

[![tests](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/ci.yml/badge.svg)](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/ci.yml)
[![daily](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/daily.yml/badge.svg)](https://github.com/Pragadeesh-19/media-quotes/actions/workflows/daily.yml)

A system that, every day, finds the BBC and Reuters articles (plus any outlet you add) that matter for understanding the trends and expectations of Russia's war on Ukraine, extracts the material quotes **verbatim**, stores them with verified metadata, and publishes a digest of the current, non-obsolete quotes for the past 14 days.

- **Dashboard:** https://pragadeesh-19.github.io/media-quotes/ (latest digest, archive, searchable quotes, run logs, database download)
- **Digests:** [`digests/`](digests/) · **Database:** [`data/mqc.db`](data/mqc.db) and [`data/quotes.json`](data/quotes.json)
- **Decisions:** [`docs/DECISIONS.md`](docs/DECISIONS.md) · **Source access, measured:** [`docs/ACCESS.md`](docs/ACCESS.md)

This README explains how I got from the brief to this design: what had to be true, what follows from that, and where measurements changed my plan.

---

## 1. What has to be true

Stripped down, the brief asks for six things to hold every day:

1. Every material article from the sources is found.
2. Every material quote in those articles is found.
3. Every stored quote is exactly what the outlet published, with correct, complete metadata.
4. The digest shows only quotes that are still current.
5. Each digest section says, in a sentence or two, what its quotes mean.
6. Anyone can check how any of the above was decided.

Two of these need judgement: "material" (2) and "still current" (4). The rest need certainty. The brief says so itself: detection must be "deterministic (not probabilistically)", verification must use "deterministic tools", the digest must contain "no change in the text of the quotes", and only the section summary is allowed to be a "non-deterministic inference".

## 2. The first principle: certainty and judgement must not mix

An LLM is the only practical way to judge whether a passage helps someone understand where the war is going. But an LLM that writes quote text can paraphrase, merge two sentences, or invent a speaker. If the model can produce text, I can only *detect* bad quotes after the fact, and detection can miss.

So I split the work by what each part is good at:

```
  STEP                                   WHO                    WHY
  Which URLs exist today per source      code                   brief 2.2 "deterministically"
  Which are about the war                code (Aho-Corasick)    deterministic, explainable, measurable
  Clean article structure                code                   site rules + structured metadata
  Which passages are material quotes     LLM -> sentence ids    needs reading comprehension
  Proposed quote is real and complete    code (verifier)        brief 2.3 "deterministic tools"
  Section of each quote                  LLM, fixed list        judgement, output constrained
  Which quotes are obsolete              LLM proposes,          judgement; code checks dates and
                                         code validates         claim types, stores the reason
  Digest layout and quote text           code                   brief 2.4 "no change in the text"
  Section summary                        LLM, labelled          brief 2.4 "non-deterministic inference"
```

The consequence that shapes everything else: **the LLM never writes quote text.** It sees the article as numbered sentences (`[b3.s1] ...`) and answers with sentence ids. Code copies the words from the stored article. A paraphrased or stitched-together quote cannot be produced, so the verifier does not have to catch one; it only has to prove that the chosen span is valid.

From this I wrote down invariants that every part of the code must keep:

| ID | Invariant | Enforced by |
|----|-----------|-------------|
| I1 | The LLM never writes quote text. It returns sentence ids; code copies the text. | Selection schema has no text field |
| I2 | A stored quote is a byte-exact, contiguous slice of one stored article. | `verify.py` |
| I3 | Detection is deterministic: same input and lexicon version, same verdict. | `relevance.py`, `filter_version` stored |
| I4 | No quote is stored with missing metadata. | Database constraints + verifier |
| I5 | The digest copies quote text and metadata unchanged; only section summaries are LLM prose, and they are labelled. | `digest.py`, tested |
| I6 | Every keep or drop decision on an article or quote is recorded with its reason. | Run logs, `articles` columns, `supersessions` |
| I7 | No access-control circumvention (paywalls, CAPTCHAs, bot walls). What cannot be reached is logged and reported, not worked around. | `fetch.py` |

## 3. Working through it, one question at a time

### Where do articles come from?

Detection starts with knowing which URLs exist. Before writing code I probed every channel:

| Channel | BBC | Reuters |
|---------|-----|---------|
| Article and section pages | 200 | 401 (bot wall), even from a home connection |
| Sitemaps | 200 | 401 |
| RSS | 200 | none reachable |
| Google News RSS | not needed | 200, but links are encrypted tokens |
| Bing News RSS | not needed | 200, and the real reuters.com URL sits in the link |

So BBC is discovered from its RSS feeds, and Reuters from Bing News, the one public channel that both lists Reuters articles and reveals their real URLs. Reuters URLs also carry their date (`...-2026-11-19/`), which serves as a last-resort publication date.

For the backfill from 1 October, the live feeds are useless because they only show recent items. I measured the alternatives: BBC topic pages ignore paging, the BBC archive sitemaps have no titles or dates, and the GDELT news API returned 429 even at one request every 12 seconds. What worked: the Wayback Machine had snapshots of BBC's world feed on 49 of the 50 days, so the backfill reads old copies of the RSS feeds.

### Which articles are relevant, without probability?

"Deterministic" rules out embeddings and LLM classifiers. A keyword list is deterministic but naive: "Russia" alone matches stories about Siberian plane crashes, and one passing mention of "Ukraine" in an art auction story should not count.

So the lexicon has two tiers. **Anchor terms** (Ukraine, Kyiv, Zelensky, Donbas, Crimea, front-line towns) qualify an article when they appear in the title, summary or URL, or when they appear densely enough in the body (hits per 1,000 words). **Context terms** (Russia, Kremlin, sanctions, NATO) only add to the score. All terms are compiled into one Aho-Corasick automaton (pyahocorasick), which scans each field once regardless of how many terms there are, with word-boundary checks so "war" does not match "Warsaw".

The thresholds favour recall, because a false positive costs one LLM call that returns no quotes, while a false negative is a material article lost for good. Every verdict is stored with its score and matched terms, so "why was this article dropped?" always has an answer. The first live run kept "Trump shakes hands with Putin and Xi" only because Ukraine came up densely in the body, which is the case the density rule exists for.

### How do you read an article as data?

Pointer-based quotes need the article stored in a form where a sentence id always means the same text. Each article becomes ordered blocks (paragraph, heading, list item, blockquote) taken from the outlet's body container, so captions, video notices, promos and "Read more" links never enter the text. Sentences are split with pysbd, which keeps "Mr.", "U.S." and decimals intact and never splits inside a quotation. Metadata comes from the page's own structured data (JSON-LD), falling back to the feed date and then the date in the URL.

One measurement changed the text handling: Reuters text contains invisible zero-width characters inside words. Left in, quotes would contain bytes nobody can see, and verbatim matching breaks. They are removed at extraction, so the stored text is exactly what a reader sees.

PDFs (common for think tanks) are converted into the same paragraph structure, so they go through the same path as HTML.

### How can an LLM choose quotes without changing them?

The model receives numbered sentences and returns spans with labels: `{start: "b4.s1", end: "b5.s0", kind, speaker, section, topic_key, claim_type}`. Code slices the text, then checks the span:

| Rule | Checks |
|------|--------|
| V1 | Sentence ids exist; the span is contiguous, at most 5 sentences and 2 adjacent paragraphs |
| V2 | Stored text equals the article slice byte for byte (re-checkable at any time) |
| V3 | Quotation marks are balanced, and the span does not start inside someone else's quotation |
| V4 | Only quotable blocks (no headings); no ads, captions, page numbers or table pipes |
| V5 | Title, link, outlet and publication date are present |
| V6 | The link is on the source's own domain; a named speaker must appear verbatim in the quote or the two paragraphs before it |

The live runs refined V6. Most rejections were valid quotes where the model wrote "Vladimir Putin" and the article said "Putin". The quote text was correct; only the attribution was unverifiable. Now the quote is kept with the speaker dropped, and the dropped name is logged. No unverified attribution reaches the database, and valid quotes are not lost.

### What does "obsolete" mean?

"Russian forces are 2 km from the town" becomes obsolete when a later article says "Russia captured the town". But "Ukraine struck the Crimean Bridge on 17 November" never becomes obsolete: it happened. So obsolescence depends on what kind of claim a quote makes.

Each quote is labelled with a `topic_key` and a `claim_type` (`state`, `event`, `expectation`, `opinion`). For quotes on the same topic in the 14-day window, the LLM proposes "old is superseded by new" pairs with a reason. Code accepts a pair only if the newer quote really is later and the older claim is a `state` or `expectation`. Accepted pairs are stored with the reason, so every hidden quote can be explained. In practice the rule has already refused an LLM proposal to hide an *event* ("a meeting took place").

### How does the digest stay verbatim?

The digest is rendered from the database by code: fixed section order, then newest first. Quote text and metadata are copied, never rewritten, and a test checks that every quote in the Markdown is byte-equal to its database row. The only LLM text is the one-to-two sentence summary under each section heading, labelled *LLM summary*, and checked for length.

### What happens when things fail?

Things failed constantly, and each failure became a rule:

- **Network:** retries with exponential backoff, honouring `Retry-After`, one request per second per host.
- **Unreachable content:** each article carries a status (`discovered`, `relevant`, `irrelevant`, `done`, `unreachable`, `failed`) with a reason and attempt count. Every run resumes unfinished articles, so a crash loses nothing.
- **Reuters text:** reuters.com returns 401, and Wayback had a readable copy of only 1 in 13 Reuters articles. Licensed partners (AOL, Yahoo UK) republish Reuters wire copy, so the fetch chain tries them last. A partner page is accepted only if its canonical link is its own, it credits Reuters, and it opens with the Reuters lead taken from the feed. That check rejected a "Yahoo" page that was really an Independent live blog quoting Reuters. Quotes keep the reuters.com link, headline and date, and the digest marks them "via syndication copy".
- **Free-tier LLM:** on the day I built this, every full Gemini Flash model answered 503 while the Lite models answered. The model setting is an ordered fallback list; a model that fails on capacity, or times out on consecutive calls, is skipped for the rest of the run. When no model is available, LLM work stops cleanly and the next run continues the queue.

### How can anyone trust it?

- **Every decision is logged.** `logs/<run_id>.jsonl` records each discovery, relevance verdict with matched terms, rejected quote with the rule that rejected it, dropped speaker, and supersession with its reason.
- **Git history is an audit trail.** The daily job commits the database, the JSON export and the digest, so each day's additions are a visible diff.
- **Tests.** 93 tests run on every push without network access, against saved real BBC, AOL and Bing pages. They cover every verification rule (including frankenquote attempts), the fetch chain under 401, 429 and timeouts, supersession, verbatim rendering, backfill, onboarding and PDFs.
- **Evaluation.** `mqc eval template --date D` writes a blind labelling file for a day (the system's decisions are not shown). `mqc eval score` reports precision and recall for article detection and, at sentence level, quote selection. No hand-labelled days are committed yet, so this repository makes no recall or precision claim.

## 4. What I built

```
config/sources/*.json
  -> discovery    RSS, Bing News (Reuters), archived feed snapshots (backfill)
  -> relevance    Aho-Corasick lexicon, deterministic and versioned
  -> fetch        direct -> Wayback -> verified partner copy -> logged as unreachable
  -> extract      HTML or PDF -> blocks -> sentences b{block}.s{n}; JSON-LD metadata
  -> select       LLM returns sentence-id spans + section, topic, claim type
  -> verify       V1..V6 -> SQLite
  -> digest       14-day window -> supersession -> Markdown -> LLM section summaries
  -> publish      data/quotes.json, digests/, logs/, dashboard (GitHub Pages)
```

**Storage.** SQLite is the database: the data is relational (quotes belong to articles, which belong to sources), constraints enforce I4, and it ships as one file anyone can open. Every run also exports `data/quotes.json`, one self-contained object per quote with all its metadata, which is what an LLM context wants. A graph database would have been a server to run for what is one table of supersession pairs. `quote_id` is a hash of the article and the exact quote text, so re-runs never duplicate a quote.

| Table | Holds |
|-------|-------|
| `sources` | Outlets |
| `articles` | Canonical URL, title, publication date and its origin, status and reason, relevance score and terms, where the text was read |
| `blocks` | The cleaned article body, paragraph by paragraph: what every quote is verified against |
| `quotes` | Text, sentence span, kind, speaker, section, topic, claim type, extraction time, run id |
| `supersessions` | Obsolete pairs with the reason |
| `runs` | Every command run with its statistics |

**Adding an outlet.** `mqc add-source <homepage>` reads the homepage, finds the RSS feeds it advertises (or tries common feed paths), keeps feeds whose items are on the outlet's own domain, proves one article extracts cleanly, and writes `config/sources/<id>.json`. `--backfill-from 2026-10-01` then collects that outlet's quotes. The Kyiv Independent was added with one command. ISW was my first choice, but it returns 403 to an honestly identified client, and `add-source` reports that instead of guessing.

## 5. Requirement trace

| Brief | Requirement | Where |
|-------|-------------|-------|
| 2.1 | Database with quote ID, text, source, publication date, article name, link, extraction date | `mqc/store.py` |
| 2.1 | Storage efficient for LLM use | `data/quotes.json` |
| 2.2 | Daily, deterministic detection of relevant articles | `daily.yml`, `discovery.py`, `relevance.py` |
| 2.2 | Read articles and reason about quotes | `selection.py`, `prompts.py` |
| 2.3 | Extract and deterministically verify quotes and metadata | `extract.py`, `verify.py` |
| 2.3 | Keep the original formatting | Typed blocks; multi-paragraph quotes keep their breaks |
| 2.3 | Quotes from 2026-10-01 to today | `mqc backfill` (done) |
| 2.3 plus | Any past period on request | `mqc backfill --start 2026-08-01 --end 2026-09-30` |
| 2.3 plus | Name a new outlet, get a pipeline | `mqc add-source` |
| 1 plus | Other authoritative sources | The Kyiv Independent |
| 2.4 | Daily 14-day digest: sections, verbatim quotes with source, publication, article and link, no obsolete quotes, LLM summary per section | `digest.py` |
| 3 | Auditable database, dashboard, GitHub | SQLite + JSON in git, `site.py`, GitHub Pages |
| 4 | Detection and selection quality | `mqc eval` |
| 4 | Network errors, unavailable content, any size or type | Retries, fetch chain, resumable statuses, chunking, PDF |
| 4 | Logs for traceability | `logs/*.jsonl` |

## 6. Running it

```bash
python -m venv .venv && .venv/Scripts/activate          # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"
export MQC_LLM_API_KEY=...                              # Gemini API key; the free tier works

mqc daily                                               # discover, collect, write today's digest
mqc backfill --start 2026-08-01 --end 2026-09-30        # collect a past period
mqc add-source https://kyivindependent.com/ --backfill-from 2026-10-01
mqc digest --date 2026-11-19                            # rebuild one digest from the database
mqc site                                                # build the dashboard into site/
mqc eval template --date 2026-11-18                     # blank labelling file for one day
mqc eval score                                          # precision and recall against the labels
pytest                                                  # 93 tests, no network needed
```

The LLM can be any OpenAI-compatible endpoint. `MQC_LLM_MODELS` is an ordered fallback list (default `gemini-3.8-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite`), and responses are cached by request hash.

`.github/workflows/daily.yml` runs every day at 06:00 UTC: it collects, writes the digest, commits `data/`, `digests/` and `logs/`, and publishes the dashboard. Run it by hand with `start`, `end` and optionally `source` to backfill a period.

## 7. Known limits

- **Reuters coverage is partial.** It depends on finding a licensed partner copy, and older Reuters articles only go back as far as Bing's news index. Unreadable Reuters articles are listed with title, date and link, not hidden.
- **Free-tier LLM capacity.** Gemini's free tier often answers 503 or 429, so collection catches up over several daily runs rather than in one. Most quotes so far were selected by the Lite fallback models, which pick generously; the evaluation is how to measure that.
- **Outlets that block automated clients** cannot be added (ISW returned 403).
- **Paywalled outlets** (FT, Bloomberg, The Economist) are out of reach by design (I7).
