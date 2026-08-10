"""Text splitting and normalisation.

Deliberately dense in string literals, string-method calls, membership tests and
loop-control statements -- operator families the original corpus produced almost
no instances of.
"""

STOP_WORDS = {"the", "a", "an", "and", "or", "of"}
PUNCTUATION = ".,;:!?"


def normalize(word: str) -> str:
    return word.strip().lower()


def strip_punctuation(word: str) -> str:
    cleaned = word
    while cleaned and cleaned[-1] in PUNCTUATION:
        cleaned = cleaned[:-1]
    return cleaned


def tokenize(text: str, keep_stop_words: bool = False) -> list:
    tokens = []
    for raw in text.split(" "):
        word = strip_punctuation(normalize(raw))
        if not word:
            continue
        if not keep_stop_words and word in STOP_WORDS:
            continue
        tokens.append(word)
    return tokens


def first_long_word(text: str, min_length: int = 4) -> str:
    for word in tokenize(text, keep_stop_words=True):
        if len(word) >= min_length:
            return word
    return ""
