"""Text normalisation, hashing and similarity used by deduplication and entity matching.

Stdlib only and deliberately simple: every similarity decision is stored alongside the score that
produced it, so the algorithm has to be explainable more than it has to be clever.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)

# Cross-language stopwords. Deliberately short — over-stripping makes distinct headlines collide.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "and", "or", "to", "for", "is", "are", "was", "were",
    "with", "by", "at", "as", "from", "that", "this", "it", "its",
    "og", "i", "af", "til", "med", "er", "en", "et", "den", "det", "som", "på",
    "der", "die", "das", "und", "von", "im", "des", "le", "la", "les", "de", "du", "el", "los", "las", "y", "que",
}


def normalise(text: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = _NON_WORD.sub(" ", s)
    return _WS.sub(" ", s).strip()


def body_hash(body: str | None) -> str | None:
    """sha256 of the normalised body. None for absent or trivially short bodies.

    A hash over a two-word stub would cluster unrelated articles, so bodies under 40 normalised
    characters are treated as "no body captured".
    """
    n = normalise(body)
    if len(n) < 40:
        return None
    return hashlib.sha256(n.encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tokens(text: str | None, drop_stopwords: bool = True) -> list[str]:
    words = normalise(text).split()
    if drop_stopwords:
        words = [w for w in words if w not in _STOPWORDS]
    return words


def token_set_ratio(a: str | None, b: str | None) -> float:
    """Jaccard-style token-set similarity in [0, 1].

    Order-insensitive, which is what headline syndication needs: outlets reorder and re-punctuate
    the same headline constantly, but the content words survive.
    """
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    intersection = len(ta & tb)
    union = len(ta | tb)
    return intersection / union if union else 0.0


def shingles(text: str | None, size: int = 5) -> set[str]:
    """Word n-grams, used for near-duplicate body detection where hashes differ by an edit."""
    words = tokens(text, drop_stopwords=False)
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def shingle_similarity(a: str | None, b: str | None, size: int = 5) -> float:
    sa, sb = shingles(a, size), shingles(b, size)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def word_count(text: str | None) -> int | None:
    if not text:
        return None
    return len(str(text).split())


def domain_of(url: str | None) -> str | None:
    """Bare hostname of a URL, without scheme, port, or leading www."""
    if not url:
        return None
    raw = str(url).strip()
    if "//" in raw:
        raw = raw.split("//", 1)[1]
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("@")[-1].split(":")[0].lower()
    if raw.startswith("www."):
        raw = raw[4:]
    return raw or None


def first_sentence_containing(body: str | None, needle_start: int, max_len: int = 400) -> str:
    """The sentence around a character offset — the evidence that a mention is what we say it is."""
    if not body:
        return ""
    text = str(body)
    start = max(0, needle_start)
    left = max(
        text.rfind(". ", 0, start), text.rfind("! ", 0, start),
        text.rfind("? ", 0, start), text.rfind("\n", 0, start),
    )
    left = 0 if left < 0 else left + 1
    candidates = [i for i in (text.find(". ", start), text.find("! ", start),
                              text.find("? ", start), text.find("\n", start)) if i != -1]
    right = min(candidates) + 1 if candidates else len(text)
    return _WS.sub(" ", text[left:right]).strip()[:max_len]
