from mqc.relevance import RelevanceFilter


def test_anchor_in_title_is_relevant(lexicon):
    verdict = RelevanceFilter(lexicon).judge("Crimean Bridge attacked again", "", "")
    assert verdict.relevant
    assert verdict.matched == {"crimean": 1}


def test_russia_alone_in_headline_is_not_enough(lexicon):
    verdict = RelevanceFilter(lexicon).judge("Russian military plane crashes", "All 19 on board died", "",
                                              body="The plane crashed near Moscow. " * 20)
    assert not verdict.relevant


def test_dense_body_mentions_make_an_unanchored_headline_relevant(lexicon):
    body = "Trump and Putin discussed Ukraine. Kyiv was not invited. " * 5 + "Other words here. " * 30
    verdict = RelevanceFilter(lexicon).judge("Trump shakes hands with Putin and Xi", "", "", body=body)
    assert verdict.relevant
    assert "density" in verdict.reason


def test_single_passing_mention_in_long_body_is_not_relevant(lexicon):
    body = "Van Gogh painting sold at auction for a record price. " * 100 + "A buyer from Ukraine bid early."
    verdict = RelevanceFilter(lexicon).judge("Van Gogh painting sells for record", "", "", body=body)
    assert not verdict.relevant


def test_matches_respect_word_boundaries(lexicon):
    verdict = RelevanceFilter(lexicon).judge("Warsaw warns on warehouse prices", "", "")
    assert "war" not in verdict.matched


def test_url_slug_counts_as_a_field(lexicon):
    verdict = RelevanceFilter(lexicon).judge("Talks resume", "", "https://www.reuters.com/world/europe/kyiv-talks-2026-11-16/")
    assert verdict.relevant


def test_verdict_is_deterministic_and_versioned(lexicon):
    f = RelevanceFilter(lexicon)
    assert f.judge("Kyiv under attack", "s", "u", "b") == f.judge("Kyiv under attack", "s", "u", "b")
    assert f.judge("Kyiv", "", "").filter_version == "war-v1"
