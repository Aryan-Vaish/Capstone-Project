"""Oracle suite for text_tokenizer."""
import pytest

from text_tokenizer import (
    PUNCTUATION,
    STOP_WORDS,
    first_long_word,
    normalize,
    strip_punctuation,
    tokenize,
)


def test_normalize_strips_and_lowercases():
    assert normalize("  Hello ") == "hello"
    assert normalize("WORLD") == "world"
    # kills lower() -> upper() and strip() -> lstrip()/rstrip()
    assert normalize("  MiXeD  ") == "mixed"
    assert normalize("\tpadded\n") == "padded"


@pytest.mark.parametrize("mark", list(PUNCTUATION))
def test_strip_punctuation_handles_every_mark(mark):
    assert strip_punctuation("word" + mark) == "word"


def test_strip_punctuation_removes_runs():
    # kills a while -> if collapse, and the [:-1] slice mutations
    assert strip_punctuation("word...") == "word"
    assert strip_punctuation("word?!") == "word"


def test_strip_punctuation_leaves_clean_words():
    assert strip_punctuation("word") == "word"
    assert strip_punctuation("") == ""


def test_strip_punctuation_does_not_touch_leading_marks():
    assert strip_punctuation(".word") == ".word"


def test_tokenize_splits_and_normalizes():
    assert tokenize("Hello World") == ["hello", "world"]


def test_tokenize_drops_every_stop_word():
    assert tokenize(" ".join(sorted(STOP_WORDS))) == []


def test_tokenize_keeps_stop_words_when_asked():
    # kills the `not keep_stop_words` mutations and the default-arg change
    assert tokenize("the cat", keep_stop_words=True) == ["the", "cat"]


def test_tokenize_skips_empty_fragments():
    # kills continue -> break: a break would truncate everything after the gap
    assert tokenize("cat  dog") == ["cat", "dog"]
    assert tokenize("  cat  ") == ["cat"]


def test_tokenize_empty_text():
    assert tokenize("") == []


def test_tokenize_strips_punctuation_per_token():
    assert tokenize("cat, dog.") == ["cat", "dog"]


def test_first_long_word_default_min_length_is_four():
    # kills min_length=4 -> 5
    assert first_long_word("a be cat door") == "door"
    assert first_long_word("a be cat") == ""


def test_first_long_word_boundary_is_inclusive():
    # kills >= -> >
    assert first_long_word("abcd") == "abcd"


def test_first_long_word_respects_explicit_min_length():
    assert first_long_word("cat door", min_length=3) == "cat"


def test_first_long_word_returns_first_not_last():
    assert first_long_word("door window") == "door"
