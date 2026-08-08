"""Sentence-aware chunking for the retrieval corpus.

Embedding a whole document dilutes it: a 600-word article about fever averages
into a vector that is weakly similar to everything and strongly similar to
nothing. Splitting into overlapping passages keeps each vector about one idea.

Boundaries land on sentence ends rather than character counts, because a chunk
cut mid-sentence retrieves badly and reads worse when it is shown as a
citation. Consecutive chunks overlap by a sentence or two so a fact stated at
a boundary is not lost from both sides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# MiniLM truncates at 256 word-pieces. Roughly 700 characters of English keeps
# most chunks under that without needing the tokeniser here.
TARGET_CHARS = 700
MIN_CHARS = 220
OVERLAP_SENTENCES = 1

# Split on terminators followed by whitespace, but not on the common
# abbreviations and decimal numbers that appear in clinical text.
_SENTENCE = re.compile(
    r"(?<![A-Z][a-z]\.)(?<!\bDr\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bNo\.)"
    r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)(?<!\d\.\d)"
    r"(?<=[.!?])\s+"
)


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    index: int
    total: int


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split((text or "").strip()) if s.strip()]


def chunk_text(text: str, *, target: int = TARGET_CHARS) -> list[Chunk]:
    """Split into overlapping, sentence-aligned passages.

    Short documents are returned whole — most of this corpus is already about
    one idea, and splitting a 300-character entry only weakens it.
    """
    body = (text or "").strip()
    if not body:
        return []
    if len(body) <= target:
        return [Chunk(body, 0, 1)]

    sentences = split_sentences(body)
    if len(sentences) <= 1:
        return [Chunk(body, 0, 1)]

    passages: list[str] = []
    current: list[str] = []
    size = 0

    for sentence in sentences:
        # Start a new passage once adding this sentence would overshoot, but
        # only if what we have is substantial enough to stand alone.
        if current and size + len(sentence) > target and size >= MIN_CHARS:
            passages.append(" ".join(current))
            current = current[-OVERLAP_SENTENCES:] if OVERLAP_SENTENCES else []
            size = sum(len(s) + 1 for s in current)
        current.append(sentence)
        size += len(sentence) + 1

    if current:
        tail = " ".join(current)
        # A short trailing remnant is folded back rather than left as its own
        # near-empty vector.
        if passages and len(tail) < MIN_CHARS:
            passages[-1] = f"{passages[-1]} {tail}".strip()
        else:
            passages.append(tail)

    return [Chunk(text=p, index=i, total=len(passages)) for i, p in enumerate(passages)]
