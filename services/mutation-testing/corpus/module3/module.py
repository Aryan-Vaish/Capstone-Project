def validate_username(username: str) -> bool:
    if username is None:
        return False
    if len(username) < 3 or len(username) > 20:
        return False
    for ch in username:
        if not (ch.isalnum() or ch == "_"):
            return False
    return True


def count_vowels(text: str) -> int:
    vowels = "aeiouAEIOU"
    count = 0
    for ch in text:
        if ch in vowels:
            count += 1
    return count
