import re
from collections import Counter
from dataclasses import dataclass

import ahocorasick

_WORD_CHAR = re.compile(r"\w")
_URL_SEPARATORS = re.compile(r"[-_/.]+")


@dataclass(frozen=True)
class Verdict:
    relevant: bool
    score: float
    reason: str
    matched: dict[str, int]
    filter_version: str


class RelevanceFilter:
    """Deterministic article filter.

    An article is relevant when a war-anchor term (Ukraine, Kyiv, Zelensky, front-line places) appears in the
    title, summary or URL, or when anchor terms reach a minimum density in the body. Context terms (Russia,
    Kremlin, sanctions) only add to the score, so a story mentioning Russia alone does not qualify.
    """

    def __init__(self, lexicon: dict):
        self.version = lexicon["version"]
        self._field_weights = lexicon["field_weights"]
        self._min_density = lexicon["min_body_anchor_density"]
        self._automaton = ahocorasick.Automaton()
        terms = [(t, True, lexicon["anchor_weight"]) for t in lexicon["anchor_terms"]]
        terms += [(t, False, lexicon["context_weight"]) for t in lexicon["context_terms"]]
        for term, is_anchor, weight in terms:
            self._automaton.add_word(term.lower(), (term.lower(), is_anchor, weight))
        self._automaton.make_automaton()

    def judge(self, title: str, summary: str, url: str, body: str = "") -> Verdict:
        fields = {"title": title, "summary": summary, "url": _URL_SEPARATORS.sub(" ", url), "body": body}
        score = 0.0
        matched: Counter[str] = Counter()
        anchor_hits = Counter()
        for field, text in fields.items():
            for term, is_anchor, weight in self._matches(text):
                score += weight * self._field_weights[field]
                matched[term] += 1
                anchor_hits[field] += is_anchor
        words = len(body.split())
        density = anchor_hits["body"] * 1000 / words if words else 0.0
        headline_hit = any(anchor_hits[f] for f in ("title", "summary", "url"))
        if headline_hit:
            relevant, reason = True, "anchor term in title, summary or url"
        elif density >= self._min_density:
            relevant, reason = True, f"body anchor density {density:.1f}/1000 words"
        else:
            relevant, reason = False, f"no headline anchor; body anchor density {density:.1f}/1000 words"
        return Verdict(relevant, round(score, 2), reason, dict(sorted(matched.items())), self.version)

    def prefilter(self, title: str, summary: str, url: str) -> Verdict:
        """Cheap pre-fetch check used for backfill: keep any candidate whose title, summary or URL has a war term."""
        fields = f"{title} {summary} {_URL_SEPARATORS.sub(' ', url)}"
        matched = Counter(term for term, _, _ in self._matches(fields))
        if matched:
            return Verdict(True, float(sum(matched.values())), "prefilter: war term in title, summary or url",
                           dict(sorted(matched.items())), self.version)
        return Verdict(False, 0.0, "prefilter: no war term in title, summary or url", {}, self.version)

    def _matches(self, text: str):
        lowered = text.lower()
        for end, (term, is_anchor, weight) in self._automaton.iter(lowered):
            start = end - len(term) + 1
            before = lowered[start - 1] if start > 0 else " "
            after = lowered[end + 1] if end + 1 < len(lowered) else " "
            if not _WORD_CHAR.match(before) and not _WORD_CHAR.match(after):
                yield term, is_anchor, weight
