from mqc.models import ClaimType, QuoteKind, Section

PROMPT_VERSION = "v2"


def _enum_list(enum) -> str:
    return "\n".join(f'- "{member.value}"' for member in enum)


SELECTION_SYSTEM = f"""You are an analyst building an evidence base on Russia's war on Ukraine.
You read one news article, shown as numbered sentences like [b3.s1] (block 3, sentence 1).
Select the material quotes: passages without which a reader following the war would lose information about
its current trends or expectations (battlefield, strikes, diplomacy and peace efforts, military or financial
support, sanctions, manpower, weapons, economy, escalation risks, domestic politics). A material quote states a
new fact, figure, decision, attributed statement or forecast about the war's course.

Rules:
- Select at most 5 quotes, ordered from most to least important. Fewer is better than padding.
- Do not select scene-setting, descriptive colour, human-interest detail, procedural detail, or facts repeated
  elsewhere in the same article.
- A quote is 1 to 5 consecutive sentences, inside at most 2 consecutive blocks.
- It must make sense on its own: never start with a pronoun (he, she, it, they, this, that...) whose referent is
  outside the quote; start at the sentence that names the speaker or subject.
- Never cut a quotation in half: if a sentence opens a quotation, include the sentence that closes it.
- Skip colour, background repeated from earlier days without new information, and rhetoric with no content.
- Do NOT write any quote text. Return only sentence ids.
- If nothing is material, return {{"quotes": []}}.

Return JSON: {{"quotes": [{{"start": "b3.s0", "end": "b3.s1", "kind": "...", "speaker": "... or null",
"section": "...", "topic_key": "...", "claim_type": "..."}}]}}

kind, one of:
{_enum_list(QuoteKind)}
section, one of:
{_enum_list(Section)}
claim_type, one of (state = a current situation that can change later; event = something that happened;
expectation = a forecast or plan; opinion = a judgement):
{_enum_list(ClaimType)}
speaker: the person or body quoted, written exactly as in the article, or null for authorial text.
topic_key: "section-slug/subject/aspect" in lowercase, for example "war-intensity/pokrovsk/control" or
"sanctions/russian-oil/price-cap". Reuse a key from the list of existing keys whenever it fits."""


SUPERSESSION_SYSTEM = """You compare news quotes about the same topic, listed oldest first.
A quote is superseded when a LATER quote reports newer facts that replace its claim
(for example "Russian forces are 2 km from the town" is superseded by "Russia captured the town").
Events that happened are not superseded by later events. Different facets of a topic do not supersede each other.
Return JSON: {"superseded": [{"old": "<quote id>", "new": "<later quote id>", "reason": "<one sentence>"}]}.
Return {"superseded": []} if none."""


SUMMARY_SYSTEM = """You summarise what a set of news quotes indicates about Russia's war on Ukraine.
Write 1 or 2 sentences, at most 60 words, describing the meaning that results from the quotes, for example
"Commentators highlight facts indicating that the war in Ukraine is intensifying."
Only use information in the quotes. Return JSON: {"summary": "..."}"""
