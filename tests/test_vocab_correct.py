# Tests for vocab_correct.py — deterministic proper-noun / jargon correction (Layer 2).
# No GPU or network needed; pure string logic over rapidfuzz.

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from parley.vocab_correct import correct_vocab

TERMS = ["Amadeus", "Switchboard", "Parley", "Kokoro", "Parusan", "gRPC", "Kubernetes"]


def test_fixes_misspelled_proper_noun():
    assert correct_vocab("I pushed to Amadies today", TERMS) == "I pushed to Amadeus today"


def test_collapses_split_word():
    assert correct_vocab("the Switch Board routes calls", TERMS) == "the Switchboard routes calls"


def test_fixes_casing_for_code_identifier():
    assert correct_vocab("we use grpc for that", TERMS) == "we use gRPC for that"


def test_exact_term_is_unchanged():
    assert correct_vocab("Amadeus is running", TERMS) == "Amadeus is running"


def test_preserves_punctuation_and_spacing():
    assert correct_vocab("Amadies, and Kokora.", TERMS) == "Amadeus, and Kokoro."


def test_no_false_positive_on_common_words():
    # Ordinary sentence with none of the terms nearby should be untouched.
    assert correct_vocab("the cat sat on the mat", TERMS) == "the cat sat on the mat"


def test_short_words_are_not_corrected():
    # "is" must not drift toward a term despite being short.
    assert correct_vocab("it is on", TERMS) == "it is on"


def test_empty_inputs_return_text():
    assert correct_vocab("", TERMS) == ""
    assert correct_vocab("hello", []) == "hello"


def test_threshold_gates_weak_matches():
    # A distant word should NOT be corrected at a strict threshold.
    assert correct_vocab("the amazon river", TERMS, threshold=0.95) == "the amazon river"


def test_default_threshold_matches_config_default():
    # The function default must agree with config.defaults.yaml so direct
    # callers and the postprocess pipeline behave identically.
    import inspect
    import re
    from pathlib import Path
    sig = inspect.signature(correct_vocab)
    yaml_text = (Path(__file__).parent.parent / "src" / "parley"
                 / "config.defaults.yaml").read_text(encoding="utf-8")
    m = re.search(r"vocab_correction:.*?threshold:\s*([\d.]+)", yaml_text, re.S)
    assert m, "vocab_correction.threshold missing from config.defaults.yaml"
    assert sig.parameters["threshold"].default == float(m.group(1))


def test_three_word_term_collapses():
    terms = ["Visual Studio Code"]
    assert correct_vocab("open visual studio code now", terms) == "open Visual Studio Code now"


def test_max_ngram_limits_span_length():
    # With max_ngram=1 the split word cannot be collapsed (needs a 2-gram).
    assert correct_vocab("the Switch Board routes", TERMS, max_ngram=1) == "the Switch Board routes"


def test_correct_term_is_not_swallowed_by_neighbor_span():
    # "Amadeus is" as a 2-gram scores close to "Amadeus", but the span already
    # contains the correct term — collapsing would delete a real word.
    assert correct_vocab("Amadeus is here", TERMS) == "Amadeus is here"


def test_adjacent_terms_both_correct():
    assert correct_vocab("Amadies runs Kokora today", TERMS) == "Amadeus runs Kokoro today"


def test_unicode_text_survives_correction():
    # Non-ASCII neighbors must be preserved verbatim around a corrected span.
    assert correct_vocab("café notes on Amadies — déjà vu", TERMS) == "café notes on Amadeus — déjà vu"
