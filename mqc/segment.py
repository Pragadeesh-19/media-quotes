import pysbd

from mqc.models import Sentence

_SEGMENTER = pysbd.Segmenter(language="en", clean=False)


def split_sentences(text: str, block_index: int) -> tuple[Sentence, ...]:
    sentences: list[Sentence] = []
    cursor = 0
    for raw in _SEGMENTER.segment(text):
        piece = raw.strip()
        if not piece:
            continue
        start = text.find(piece, cursor)
        if start < 0:
            raise ValueError(f"segmenter output not found in block {block_index}: {piece[:40]!r}")
        end = start + len(piece)
        sentences.append(Sentence(sid=f"b{block_index}.s{len(sentences)}", start=start, end=end))
        cursor = end
    return tuple(sentences)
