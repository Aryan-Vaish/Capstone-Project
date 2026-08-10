"""Weak suite -- intended tier: weak (25-40% expected kill).

Deliberately omitted: keep_stop_words=True, the min_length default and its
boundary, trailing punctuation runs, empty input, and first_long_word entirely.
"""
from text_tokenizer import normalize, strip_punctuation, tokenize


def test_normalize_lowercases():
    assert normalize("  Hello ") == "hello"


def test_strip_punctuation_removes_period():
    assert strip_punctuation("word.") == "word"


def test_tokenize_splits_words():
    assert tokenize("hello world") == ["hello", "world"]


def test_tokenize_drops_stop_words():
    assert tokenize("the cat") == ["cat"]
