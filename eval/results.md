# Evaluation

Labelled days:

- 2026-11-18: Independent model labels from article text only, blind to the system's selections.

| Stage | Precision | Recall | TP | FP | FN |
|---|---|---|---|---|---|
| Article detection, all labelled articles | 90% | 50% | 9 | 1 | 9 |
| Article detection, articles that could be read | 90% | 100% | 9 | 1 | 0 |
| Quote selection (processed articles) | 58% | 50% | 15 | 11 | 15 |

Quote matching is at sentence level: a selected quote counts as correct when it shares at least one sentence with a labelled quote.

## Misses

- quote missed in https://www.bbc.co.uk/news/articles/c32l507ev4v4o: ['b1.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c32l507ev4v4o: ['b2.s1']
- quote missed in https://www.bbc.co.uk/news/articles/c32l507ev4v4o: ['b27.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c32l507ev4v4o: ['b32.s0', 'b32.s1']
- quote missed in https://www.bbc.co.uk/news/articles/c32l507ev4v4o: ['b37.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c9n70gd320mpo: ['b10.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c9n70gd320mpo: ['b14.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c9n70gd320mpo: ['b15.s0']
- quote missed in https://www.bbc.co.uk/news/articles/c9n70gd320mpo: ['b20.s0']
- quote missed in https://www.bbc.co.uk/news/articles/ck9d75x5wwwwo: ['b6.s0']
- quote missed in https://www.bbc.co.uk/news/articles/ck9d75x5wwwwo: ['b10.s0']
- quote missed in https://www.bbc.co.uk/news/articles/ck9d75x5wwwwo: ['b11.s0']
- quote missed in https://www.bbc.co.uk/news/articles/ck9d75x5wwwwo: ['b19.s0']
- article (could not be read): https://www.reuters.com/business/aerospace-defense/operation-vivaldi-ukraine-melds-man-machine-repel-russians-2026-11-18/
- article (could not be read): https://www.reuters.com/business/energy/drone-attacks-nearly-halve-russias-november-oil-refining-sources-say-2026-11-18/
- article (could not be read): https://www.reuters.com/business/energy/putin-says-eus-russian-energy-ban-caused-humiliating-fall-eu-living-standards-2026-11-18/
- article (could not be read): https://www.reuters.com/world/china/trumps-historic-meeting-with-xi-putin-headlines-asia-trip-2026-11-18/
- article (could not be read): https://www.reuters.com/world/china/ukraines-european-allies-meet-berlin-show-support-2026-11-19/
- article (could not be read): https://www.reuters.com/world/chinas-xi-calls-free-open-trade-apec-summit-2026-11-18/
- article (could not be read): https://www.reuters.com/world/eu-proposes-new-45-billion-loan-ukraine-2027-be-repaid-by-russia-2026-11-18/
- article (could not be read): https://www.reuters.com/world/europe/russian-attacks-ukrainian-city-odesa-kill-four-officials-say-2026-11-18/
- article (could not be read): https://www.reuters.com/world/russia-says-us-despite-current-sanctions-will-likely-need-more-its-uranium-2026-11-18/
- quote missed in https://www.reuters.com/world/trump-envoys-witkoff-kushner-expected-berlin-ukraine-talks-says-us-2026-11-18/: ['b2.s0']
- quote missed in https://www.reuters.com/world/trump-envoys-witkoff-kushner-expected-berlin-ukraine-talks-says-us-2026-11-18/: ['b6.s0']
