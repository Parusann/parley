# vocab_correct.py
# Layer 2 of proper-noun accuracy: deterministic fuzzy correction. After Whisper
# transcribes, snap near-miss words/phrases back to a curated vocabulary of the user's
# terms (project names, jargon, code identifiers) using rapidfuzz. Unlike an LLM pass
# this NEVER hallucinates — it only replaces spans that closely match a known term, and
# it fixes both misspellings ("Amadies" -> "Amadeus") and split words ("Switch Board" ->
# "Switchboard"). Runs before the optional Ollama polish in the post-process pipeline.

import re
from typing import List, Optional

from rapidfuzz import fuzz, process, utils

# A "word" for matching: letters/digits/apostrophes. Everything else (spaces,
# punctuation) is preserved verbatim so we can rebuild the text unchanged except
# for the corrected spans.
_WORD_RE = re.compile(r"[A-Za-z0-9']+")

# Single tokens shorter than this are never auto-corrected — too many false positives
# (e.g. "a", "to", "the" drifting toward a short term). Multi-word phrases are exempt.
_MIN_SINGLE_WORD_LEN = 3


def correct_vocab(text: str,
                  terms: List[str],
                  threshold: float = 0.82,
                  max_ngram: Optional[int] = None) -> str:
    """Snap fuzzy matches in `text` to canonical `terms`.

    Args:
        text: transcript to correct.
        terms: curated canonical vocabulary (e.g. ["Amadeus", "Switchboard", "gRPC"]).
        threshold: similarity in [0, 1]; higher = stricter (fewer corrections). 0.82 is
            the shipped default (lets one substitution in a 6-letter name correct,
            e.g. "Kokora" -> "Kokoro" at 83.3 similarity).
        max_ngram: longest phrase (in words) to consider matching. Defaults to the word
            count of the longest term, so multi-word terms still match.

    Returns:
        The text with matched spans replaced by their canonical term. Non-matching text,
        spacing, and punctuation are preserved exactly.
    """
    if not text or not terms:
        return text
    terms = [t for t in terms if t and t.strip()]
    if not terms:
        return text

    cutoff = threshold * 100.0  # rapidfuzz scores on 0..100
    longest_term_words = max(len(t.split()) for t in terms)
    # Try at least 2-grams even for single-word terms, so a term Whisper split across two
    # tokens ("Switch Board" -> "Switchboard") can still be collapsed.
    max_words = max_ngram if max_ngram is not None else max(2, longest_term_words)

    # Lowercased forms of the terms, used to avoid swallowing a real word that sits next to
    # an already-correct term when collapsing a multi-word span (e.g. "Amadeus is").
    processed_terms = {utils.default_process(t) for t in terms}

    # Tokenize into alternating word / non-word segments so we can rebuild verbatim.
    tokens = []  # each: {"s": <str>, "word": <bool>}
    pos = 0
    for m in _WORD_RE.finditer(text):
        if m.start() > pos:
            tokens.append({"s": text[pos:m.start()], "word": False})
        tokens.append({"s": m.group(), "word": True})
        pos = m.end()
    if pos < len(text):
        tokens.append({"s": text[pos:], "word": False})

    word_positions = [i for i, t in enumerate(tokens) if t["word"]]
    consumed = [False] * len(tokens)

    # Greedy longest-first: prefer collapsing "Switch Board" over correcting "Switch" alone.
    upper_n = min(max_words, len(word_positions))
    for n in range(upper_n, 0, -1):
        wi = 0
        while wi + n <= len(word_positions):
            span = word_positions[wi:wi + n]
            if any(consumed[i] for i in span):
                wi += 1
                continue

            # Never collapse a multi-word span if one of its words is already a correct
            # term — those neighbouring words are real, not a split term.
            if n > 1 and any(utils.default_process(tokens[i]["s"]) in processed_terms for i in span):
                wi += 1
                continue

            phrase = " ".join(tokens[i]["s"] for i in span)
            if n == 1 and len(phrase) < _MIN_SINGLE_WORD_LEN:
                wi += 1
                continue

            # Case-insensitive normalized Levenshtein: predictable, and default_process
            # lowercases both sides so "grpc" matches "gRPC" (and returns canonical casing).
            match = process.extractOne(
                phrase, terms,
                scorer=fuzz.ratio, processor=utils.default_process, score_cutoff=cutoff,
            )
            if match is not None:
                canonical = match[0]
                # Only rewrite if it actually differs (also fixes casing, e.g. "grpc" -> "gRPC").
                if phrase != canonical:
                    tokens[span[0]]["s"] = canonical
                    # Blank the trailing words AND the separators between them so an
                    # n-gram collapses to the single canonical token.
                    for j in range(span[0] + 1, span[-1] + 1):
                        tokens[j]["s"] = ""
                for i in span:
                    consumed[i] = True
                wi += n
            else:
                wi += 1

    return "".join(t["s"] for t in tokens)
