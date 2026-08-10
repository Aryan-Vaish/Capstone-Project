"""
Taxonomy v2 classifier tests.

Every diff below is in the exact format `mutmut show` emits: a unified diff of
the *isolated function* rendered as its own module (see
mutmut/__main__.py:1691-1714), which is why the hunk headers start at line 1
regardless of where the function sits in its file.

These run anywhere -- no mutmut, no WSL -- which is the whole reason the
classifier was split out of generator.py.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from mutators import taxonomy as tx
from mutators.classifier import classify_diff


def diff(before: str, after: str, ctx_before: str = "", ctx_after: str = "") -> str:
    """Build a mutmut-shaped unified diff around a single changed line."""
    lines = ["--- corpus/m.py", "+++ corpus/m.py", "@@ -1,4 +1,4 @@"]
    if ctx_before:
        lines.append(" " + ctx_before)
    lines.append("-" + before)
    lines.append("+" + after)
    if ctx_after:
        lines.append(" " + ctx_after)
    return "\n".join(lines)


@pytest.mark.parametrize("before,after,expected", [
    # --- operator_swap_op: comparison is boundary-only in mutmut ---
    ("    if amount > balance:", "    if amount >= balance:", tx.COMPARISON_BOUNDARY),
    ("    if price < 0:", "    if price <= 0:", tx.COMPARISON_BOUNDARY),
    ("    return cart_total >= threshold", "    return cart_total > threshold", tx.COMPARISON_BOUNDARY),
    ("    if len(u) <= 3:", "    if len(u) < 3:", tx.COMPARISON_BOUNDARY),

    # --- equality ---
    ("    if ch == '_':", "    if ch != '_':", tx.EQUALITY_NEGATION),
    ("    if state != 'open':", "    if state == 'open':", tx.EQUALITY_NEGATION),

    # --- logical connectors ---
    ("    if a < 0 or b > 1:", "    if a < 0 and b > 1:", tx.LOGICAL_CONNECTOR_SWAP),
    ("    if x and y:", "    if x or y:", tx.LOGICAL_CONNECTOR_SWAP),

    # --- arithmetic ---
    ("    return balance - amount", "    return balance + amount", tx.ARITHMETIC_REPLACEMENT),
    ("    total = price * qty", "    total = price / qty", tx.ARITHMETIC_REPLACEMENT),
    ("    mid = n // 2", "    mid = n / 2", tx.ARITHMETIC_REPLACEMENT),
    ("    r = year % 4", "    r = year / 4", tx.ARITHMETIC_REPLACEMENT),
    ("    d = base ** n", "    d = base * n", tx.ARITHMETIC_REPLACEMENT),

    # --- bitwise ---
    ("    return flags & MASK", "    return flags | MASK", tx.BITWISE_REPLACEMENT),
    ("    return a | b", "    return a & b", tx.BITWISE_REPLACEMENT),
    ("    return a ^ b", "    return a & b", tx.BITWISE_REPLACEMENT),
    ("    return v << 2", "    return v >> 2", tx.BITWISE_REPLACEMENT),

    # --- augmented assignment: swap vs. collapse to plain `=` ---
    ("    count += 1", "    count -= 1", tx.AUGMENTED_ASSIGN_SWAP),
    ("    acc *= f", "    acc /= f", tx.AUGMENTED_ASSIGN_SWAP),
    ("    count += 1", "    count = 1", tx.AUGMENTED_ASSIGN_REMOVAL),

    # --- constants ---
    ("    count = 0", "    count = 1", tx.NUMERIC_CONSTANT_CHANGE),
    ("    threshold = 50.0", "    threshold = 51.0", tx.NUMERIC_CONSTANT_CHANGE),
    ('    vowels = "aeiou"', '    vowels = "XXaeiouXX"', tx.STRING_LITERAL_CHANGE),
    ('    sep = "AB"', '    sep = "ab"', tx.STRING_LITERAL_CHANGE),
    ("    flag = True", "    flag = False", tx.BOOLEAN_CONSTANT_FLIP),
    ("    return False", "    return True", tx.BOOLEAN_CONSTANT_FLIP),

    # --- identity / membership (insert or delete of `not`) ---
    ("    if username is None:", "    if username is not None:", tx.IDENTITY_NEGATION),
    ("    if x is not None:", "    if x is None:", tx.IDENTITY_NEGATION),
    ("    if ch in vowels:", "    if ch not in vowels:", tx.MEMBERSHIP_NEGATION),
    ("    if k not in seen:", "    if k in seen:", tx.MEMBERSHIP_NEGATION),

    # --- unary removal ---
    ("    if not ch.isalnum():", "    if ch.isalnum():", tx.UNARY_OPERATOR_REMOVAL),

    # --- loop control (note mutmut's asymmetry: break->return, continue->break) ---
    ("        break", "        return", tx.LOOP_CONTROL_SWAP),
    ("        continue", "        break", tx.LOOP_CONTROL_SWAP),

    # --- string method swaps ---
    ("    return s.lower()", "    return s.upper()", tx.STRING_METHOD_SWAP),
    ("    return s.lstrip()", "    return s.rstrip()", tx.STRING_METHOD_SWAP),
    ("    return s.split(',', 2)", "    return s.rsplit(',', 2)", tx.STRING_METHOD_SWAP),

    # --- assignment collapsed to None ---
    ("    discounted = price - fee", "    discounted = None", tx.ASSIGNMENT_TO_NONE),

    # --- call argument replaced by None ---
    ("    return round(discounted, 2)", "    return round(None, 2)", tx.ARGUMENT_REPLACEMENT),
])
def test_operator_classification(before, after, expected):
    result = classify_diff(diff(before, after))
    assert result.operator == expected, (
        f"{before.strip()!r} -> {after.strip()!r}\n"
        f"  expected {expected}, got {result.operator} ({result.detail})"
    )


def test_every_result_is_a_declared_operator():
    """The classifier must never invent a name the taxonomy does not declare."""
    samples = [
        diff("    if a > b:", "    if a >= b:"),
        diff("    x = 1", "    x = 2"),
        diff("    return f(a, b)", "    return f(b)"),
        diff("    weird ~~~ syntax", "    other ~~~ thing"),
    ]
    for d in samples:
        assert classify_diff(d).operator in tx.OPERATORS


def test_diff_marker_is_not_mistaken_for_an_operator():
    """Taxonomy v1's headline bug: the leading '-'/'+' of the diff markers were
    matched as arithmetic operators, so nearly everything looked arithmetic.
    A pure comparison change must not classify as arithmetic."""
    result = classify_diff(diff("    if amount > balance:", "    if amount >= balance:"))
    assert result.operator == tx.COMPARISON_BOUNDARY


def test_context_lines_are_ignored():
    """Only -/+ lines drive classification; surrounding context must not leak in."""
    d = diff(
        "    if amount > balance:", "    if amount >= balance:",
        ctx_before="def withdraw(balance, amount):",
        ctx_after='        raise ValueError("Insufficient funds")',
    )
    assert classify_diff(d).operator == tx.COMPARISON_BOUNDARY


def test_removed_line_is_returned_for_line_resolution():
    """The line-number resolver locates the mutant by matching this text."""
    result = classify_diff(diff("    if amount > balance:", "    if amount >= balance:"))
    assert result.removed_line.strip() == "if amount > balance:"


def test_match_case_removal():
    d = "\n".join([
        "--- corpus/m.py", "+++ corpus/m.py", "@@ -3,4 +3,3 @@",
        " def route(x):",
        "-        case 2:",
        "-            return 'b'",
    ])
    assert classify_diff(d).operator == tx.MATCH_CASE_REMOVAL


def test_unclassifiable_returns_other_not_a_crash():
    assert classify_diff("").operator == tx.OTHER
    assert classify_diff("--- a\n+++ b\n@@ -1 +1 @@").operator == tx.OTHER


def test_taxonomy_families_cover_every_operator():
    for op in tx.OPERATORS:
        assert tx.family_of(op) in tx.FAMILIES
