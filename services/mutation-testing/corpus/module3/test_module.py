from module import validate_username, count_vowels

def test_validate_username_valid():
    assert validate_username("user_123") is True

def test_validate_username_too_short():
    assert validate_username("ab") is False

def test_validate_username_none():
    assert validate_username(None) is False

def test_count_vowels_basic():
    assert count_vowels("hello") == 2
