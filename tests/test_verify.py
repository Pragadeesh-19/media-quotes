from mqc.models import Block, BlockType, ClaimType, Quote, QuoteKind, Section, Sentence, SpanProposal
from mqc.verify import Rejection, build_quote, slice_text, verify_stored
from tests.conftest import make_article

ARTICLE = make_article([
    "Russian forces advanced near Pokrovsk. Ukraine's army said the town was under heavy pressure.",
    "“We will hold the line,” said Oleksandr Syrskyi. He added that reserves had arrived.",
    "Separately, the EU agreed a new sanctions package. It targets shadow fleet tankers.",
    (BlockType.HEADING, "What happens next"),
    "Analysts expect talks to resume in December.",
])


def _proposal(start, end, **overrides):
    values = dict(start=start, end=end, kind=QuoteKind.AUTHORIAL, speaker=None, section=Section.WAR_INTENSITY,
                  topic_key="war-intensity/pokrovsk/control", claim_type=ClaimType.STATE)
    return SpanProposal(**(values | overrides))


def test_single_sentence_quote_is_sliced_verbatim(sources):
    quote = build_quote(_proposal("b0.s1", "b0.s1"), ARTICLE, sources["bbc"])
    assert isinstance(quote, Quote)
    assert quote.text == "Ukraine's army said the town was under heavy pressure."


def test_quote_across_two_adjacent_blocks_keeps_paragraph_break(sources):
    quote = build_quote(_proposal("b0.s1", "b1.s0", speaker="Oleksandr Syrskyi"), ARTICLE, sources["bbc"])
    assert quote.text == ("Ukraine's army said the town was under heavy pressure.\n\n"
                          "“We will hold the line,” said Oleksandr Syrskyi.")


def test_quote_id_is_stable_and_text_derived(sources):
    first = build_quote(_proposal("b0.s0", "b0.s0"), ARTICLE, sources["bbc"])
    second = build_quote(_proposal("b0.s0", "b0.s0", section=Section.RESOURCES), ARTICLE, sources["bbc"])
    assert first.quote_id == second.quote_id


def test_frankenquote_across_three_blocks_is_rejected(sources):
    result = build_quote(_proposal("b0.s0", "b2.s0"), ARTICLE, sources["bbc"])
    assert result == Rejection("V1", "spans more than 2 adjacent blocks")


def test_reversed_span_is_rejected(sources):
    assert build_quote(_proposal("b1.s1", "b1.s0"), ARTICLE, sources["bbc"]).rule == "V1"


def test_unknown_sentence_id_is_rejected(sources):
    assert build_quote(_proposal("b0.s0", "b9.s9"), ARTICLE, sources["bbc"]).rule == "V1"


def test_heading_is_not_quotable(sources):
    assert build_quote(_proposal("b3.s0", "b3.s0"), ARTICLE, sources["bbc"]).rule == "V4"


def test_span_that_starts_inside_a_quotation_is_rejected(sources):
    text = "“We will hold. We will win,” he said."
    block = Block(index=0, type=BlockType.PARAGRAPH, text=text,
                  sentences=(Sentence(sid="b0.s0", start=0, end=14), Sentence(sid="b0.s1", start=15, end=len(text))))
    article = ARTICLE.model_copy(update={"blocks": (block,)})
    assert build_quote(_proposal("b0.s1", "b0.s1"), article, sources["bbc"]) == Rejection(
        "V3", "unbalanced quotation marks")


def test_span_with_unclosed_quotation_is_rejected(sources):
    article = make_article(["He said: “The front is stable. Reserves have arrived."])
    assert build_quote(_proposal("b0.s0", "b0.s0"), article, sources["bbc"]).rule == "V3"


def test_span_opening_mid_quotation_with_balanced_text_is_rejected(sources):
    text = "“Hold. Allies said “yes” today. Win” he said."
    block = Block(index=0, type=BlockType.PARAGRAPH, text=text,
                  sentences=(Sentence(sid="b0.s0", start=0, end=6), Sentence(sid="b0.s1", start=7, end=31),
                             Sentence(sid="b0.s2", start=32, end=len(text))))
    article = ARTICLE.model_copy(update={"blocks": (block,)})
    assert slice_text(article, "b0.s1", "b0.s1") == "Allies said “yes” today."
    result = build_quote(_proposal("b0.s1", "b0.s1"), article, sources["bbc"])
    assert result == Rejection("V3", "span starts inside an open quotation")


def test_speaker_must_appear_in_quote_or_preceding_blocks(sources):
    result = build_quote(_proposal("b2.s0", "b2.s0", speaker="Ursula von der Leyen"), ARTICLE, sources["bbc"])
    assert result.rule == "V6"


def test_speaker_found_in_preceding_block_is_accepted(sources):
    result = build_quote(_proposal("b1.s1", "b1.s1", speaker="Oleksandr Syrskyi"), ARTICLE, sources["bbc"])
    assert isinstance(result, Quote)


def test_url_outside_source_domains_is_rejected(sources):
    article = make_article(["Kyiv was hit overnight."], url="https://example.com/news/1")
    assert build_quote(_proposal("b0.s0", "b0.s0"), article, sources["bbc"]).rule == "V6"


def test_contaminated_text_is_rejected(sources):
    article = make_article(["Kyiv was hit overnight. Advertisement continues below."])
    assert build_quote(_proposal("b0.s0", "b0.s1"), article, sources["bbc"]).rule == "V4"


def test_topic_key_is_normalized(sources):
    quote = build_quote(_proposal("b0.s0", "b0.s0", topic_key="War Intensity / Pokrovsk!! / Control"),
                        ARTICLE, sources["bbc"])
    assert quote.topic_key == "war-intensity/pokrovsk/control"


def test_stored_quote_that_no_longer_matches_article_fails(sources):
    quote = build_quote(_proposal("b0.s0", "b0.s0"), ARTICLE, sources["bbc"])
    tampered = quote.model_copy(update={"text": quote.text.replace("advanced", "retreated")})
    assert verify_stored(quote, ARTICLE) is None
    assert verify_stored(tampered, ARTICLE).rule == "V2"


def test_slice_text_of_whole_block_equals_block():
    assert slice_text(ARTICLE, "b2.s0", "b2.s1") == ARTICLE.blocks[2].text
