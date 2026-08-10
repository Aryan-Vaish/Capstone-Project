"""Oracle suite for username_validator."""
from username_validator import count_vowels, validate_username


def test_validate_username_valid():
    assert validate_username("user_123") is True


def test_validate_username_none():
    assert validate_username(None) is False


def test_validate_username_too_short():
    assert validate_username("ab") is False


def test_validate_username_min_length_boundary():
    # kills `< 3` -> `<= 3`
    assert validate_username("abc") is True


def test_validate_username_max_length_boundary():
    # kills `> 20` -> `>= 20`
    assert validate_username("a" * 20) is True
    assert validate_username("a" * 21) is False


def test_validate_username_rejects_punctuation():
    # kills removal of `not` and the isalnum/`==` mutations
    assert validate_username("user-123") is False
    assert validate_username("user 123") is False


def test_validate_username_accepts_underscore():
    # kills `ch == "_"` -> `!=` and the "_" string mutations
    assert validate_username("___") is True


def test_validate_username_empty_string():
    assert validate_username("") is False


def test_count_vowels_basic():
    assert count_vowels("hello") == 2


def test_count_vowels_counts_uppercase():
    # kills mutations of the "aeiouAEIOU" literal
    assert count_vowels("HELLO") == 2
    assert count_vowels("AEIOUaeiou") == 10


def test_count_vowels_none_present():
    assert count_vowels("rhythm") == 0


def test_count_vowels_empty():
    assert count_vowels("") == 0


def test_count_vowels_increments_by_one():
    # kills `count += 1` -> `count = 1` and `+=` -> `-=`
    assert count_vowels("aaa") == 3
    assert count_vowels("aeiou") == 5
