# Source access: measured, not assumed

All probes were run from a residential connection. Numbers come from the run logs in `logs/`.

## BBC (2026-11-19, run 20261119T123344Z-cd110e)

| Step | Result |
|------|--------|
| RSS feeds (`feeds.bbci.co.uk`) | 200 |
| Articles discovered | 50 |
| Fetched directly | 50 of 50 |
| Judged relevant by the lexicon filter | 9 |

## Reuters

| Channel | Result (2026-11-18/19) |
|---------|------------------------|
| reuters.com article and section pages | 401 (bot wall), also with a browser user agent |
| reuters.com sitemaps (`arc/outboundfeeds`) | 401 |
| Google News RSS `site:reuters.com` | 200, but links are opaque `AU_yqL...` tokens that need an undocumented Google API to decode. Not used. |
| Bing News RSS `site:reuters.com` | 200, and the real Reuters URL is in the `url=` parameter. **Used for discovery.** |
| Wayback Machine | 1 of 13 sampled articles had a readable snapshot. Most captures are 401: Reuters blocks the archive too. |
| Licensed partner copies (AOL, Yahoo UK) | Full Reuters wire text with a "(Reuters)" credit |

### End-to-end coverage (2026-11-19, run 20261119T124023Z-b3f941)

| Outcome | Articles |
|---------|----------|
| Discovered via Bing | 29 |
| Read via archive snapshot | 0 |
| Read via verified partner copy | 8 (28%) |
| Unreachable: no partner copy found | 18 |
| Unreachable: partner page rejected (canonical link elsewhere, or the Reuters lead not at the start) | 3 |

Unreachable Reuters articles stay in the database with their title, date and link. They are retried on the next two runs.

### How a partner copy is accepted

1. The Reuters lead comes from the publisher's feed entry: the Bing summary of the reuters.com page.
2. Bing News is searched for the first 12 words of that lead, restricted to the partner domains.
3. A partner page is accepted only if:
   - its canonical link is on the partner's own domain (this rejects roundups and live blogs that embed Reuters text, such as an Independent live blog served through Yahoo);
   - "(Reuters)" appears in its first two paragraphs;
   - the 12-word lead appears verbatim in those same paragraphs.
4. The stored article keeps the reuters.com URL, the Reuters headline and the feed date. `retrieval_method = syndication` and `retrieval_url` record where the text was read. The digest marks these quotes "via syndication copy".

Rejected pages and their reasons are in the run log, so every decision can be checked.

## Cumulative coverage after the backfill (2026-11-19, all runs)

| Source | Discovered | Set aside before fetching (no war term) | Read | How read | Unreachable |
|--------|-----------:|----------------------------------------:|-----:|----------|------------:|
| BBC | 1,584 | 1,343 | 241 | direct | 0 |
| The Kyiv Independent | 297 | 4 | 293 | 290 direct, 3 Wayback | 0 |
| Reuters | 81 | 2 | 24 (30% of 79) | partner copy | 55 |

The Kyiv Independent was added with `mqc add-source https://kyivindependent.com/`. ISW (understandingwar.org) was tried first: its homepage returns 403 to our client and its feeds and sub-sitemaps are blocked, so `add-source` refused it with that reason.
