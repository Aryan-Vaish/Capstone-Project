"""
Line-resolution tests -- the regression guard for the dataset-v1 blocker.

Dataset v1 stored `is_eligible_for_free_shipping`'s mutants at lines 1 and 2
when the function actually lives at file lines 10-11, because `mutmut show`
diffs the isolated function and v1 used the hunk offset verbatim. These tests
assert the real file coordinates, so that bug cannot come back silently.

No mutmut required: the diffs are synthesised in the exact format
`get_diff_for_mutant` emits (mutmut/__main__.py:1691-1714).
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from mutators.line_resolver import (
    CLASS_NAME_SEPARATOR,
    FunctionRef,
    build_function_index,
    parse_hunk_relative_line,
    parse_mutant_id,
    resolve_mutant_line,
)

CORPUS = Path(__file__).resolve().parents[1] / "corpus"

# The two migrated modules these regressions are pinned to. Their CODE is
# unchanged from the pre-migration corpus, so the line coordinates asserted
# below are the same ones dataset v1 got wrong.
DISCOUNT = CORPUS / "02_discount_pricing" / "discount_pricing.py"
USERNAME = CORPUS / "03_username_validator" / "username_validator.py"


# --------------------------------------------------------------------------
# mutant id parsing
# --------------------------------------------------------------------------

def test_parse_module_level_function():
    ref, idx = parse_mutant_id("module.x_withdraw__mutmut_3")
    assert ref == FunctionRef(function_name="withdraw", class_name=None)
    assert idx == 3


def test_parse_strips_module_qualifier_and_x_prefix():
    """v1 returned 'module.x_apply_discount' here, which is why stubs were
    named after the module rather than the function under test."""
    ref, _ = parse_mutant_id("module.x_apply_discount__mutmut_17")
    assert ref.function_name == "apply_discount"
    assert ref.qualified == "apply_discount"


def test_parse_class_method_uses_u01c1_separator():
    """mutmut mangles methods as x<SEP><Class><SEP><name>. v1 would have
    returned the raw mangled string, separator and all."""
    mid = f"rate_limiter.x{CLASS_NAME_SEPARATOR}RateLimiter{CLASS_NAME_SEPARATOR}allow__mutmut_1"
    ref, idx = parse_mutant_id(mid)
    assert ref.function_name == "allow"
    assert ref.class_name == "RateLimiter"
    assert ref.qualified == "RateLimiter.allow"
    assert idx == 1


# --------------------------------------------------------------------------
# hunk header parsing
# --------------------------------------------------------------------------

def test_hunk_header_with_comma():
    d = "--- a\n+++ b\n@@ -1,4 +1,4 @@\n def f():\n-    return 1\n+    return 2"
    assert parse_hunk_relative_line(d) == 2


def test_hunk_header_without_comma():
    """Single-line hunks emit `@@ -3 +3 @@`; v1's regex required the comma and
    silently returned None for this form."""
    d = "--- a\n+++ b\n@@ -3 +3 @@\n-    return 1\n+    return 2"
    assert parse_hunk_relative_line(d) == 3


# --------------------------------------------------------------------------
# THE regression: real corpus coordinates
# --------------------------------------------------------------------------

def _diff_for(removed: str, added: str, hunk_start: int = 1, context_before=()) -> str:
    lines = ["--- x.py", "+++ x.py", f"@@ -{hunk_start},4 +{hunk_start},4 @@"]
    lines += [" " + c for c in context_before]
    lines += ["-" + removed, "+" + added]
    return "\n".join(lines)


def test_second_function_in_file_resolves_to_absolute_lines():
    """is_eligible_for_free_shipping is at file lines 10-11.

    Dataset v1 recorded its two mutants at lines 1 and 2 -- the function-relative
    offsets. This is the exact case that made every coverage-gate decision for
    non-first functions wrong.
    """
    src = DISCOUNT
    lines = src.read_text(encoding="utf-8").splitlines()

    # sanity: the fixture really is where we think it is
    assert lines[9].startswith("def is_eligible_for_free_shipping")
    assert "cart_total >= threshold" in lines[10]

    diff = _diff_for(
        "    return cart_total >= threshold",
        "    return cart_total > threshold",
        hunk_start=1,
        context_before=["def is_eligible_for_free_shipping(cart_total: float, threshold: float = 50.0) -> bool:"],
    )
    line, ref, method = resolve_mutant_line(
        src, "module.x_is_eligible_for_free_shipping__mutmut_2", diff,
        removed_line="    return cart_total >= threshold",
    )
    assert ref.function_name == "is_eligible_for_free_shipping"
    assert line == 11, f"expected file line 11, got {line} (method={method})"
    assert method == "content_unique"


def test_def_line_mutation_resolves_to_def_line():
    """The `threshold=50.0` default lives on the def line, file line 10."""
    src = DISCOUNT
    removed = "def is_eligible_for_free_shipping(cart_total: float, threshold: float = 50.0) -> bool:"
    diff = _diff_for(removed, removed.replace("50.0", "51.0"))
    line, ref, method = resolve_mutant_line(
        src, "module.x_is_eligible_for_free_shipping__mutmut_1", diff, removed_line=removed,
    )
    assert line == 10, f"expected file line 10, got {line} (method={method})"


def test_count_vowels_body_resolves_into_its_real_span():
    """count_vowels occupies file lines 12-18; v1 stored its mutants at 2,3,5,6."""
    src = USERNAME
    lines = src.read_text(encoding="utf-8").splitlines()
    assert lines[11].startswith("def count_vowels")

    diff = _diff_for("        count += 1", "        count = 1")
    line, ref, method = resolve_mutant_line(
        src, "module.x_count_vowels__mutmut_9", diff, removed_line="        count += 1",
    )
    assert ref.function_name == "count_vowels"
    assert line == 17, f"expected file line 17, got {line} (method={method})"
    assert 12 <= line <= 18


def test_first_function_still_correct():
    """validate_username is first in its file, so v1 happened to be right here.
    Guard that the fix did not regress the previously-working case."""
    src = USERNAME
    diff = _diff_for("    if username is None:", "    if username is not None:")
    line, _, _ = resolve_mutant_line(
        src, "module.x_validate_username__mutmut_1", diff, removed_line="    if username is None:",
    )
    assert line == 2


# --------------------------------------------------------------------------
# ambiguity and failure behaviour
# --------------------------------------------------------------------------

def test_duplicate_text_is_disambiguated_by_hunk_offset(tmp_path):
    src = tmp_path / "dup.py"
    src.write_text(
        "def f(a):\n"
        "    if a:\n"
        "        return False\n"
        "    if not a:\n"
        "        return False\n"
        "    return True\n",
        encoding="utf-8",
    )
    # two identical `return False` lines (3 and 5); hunk points at the second
    diff = _diff_for("        return False", "        return True", hunk_start=4)
    line, _, method = resolve_mutant_line(
        src, "dup.x_f__mutmut_2", diff, removed_line="        return False",
    )
    assert line in (3, 5)
    assert method == "content_disambiguated"


def test_unresolvable_returns_none_rather_than_guessing(tmp_path):
    """A wrong line silently corrupts the dataset; a missing one is visible."""
    src = tmp_path / "s.py"
    src.write_text("def f():\n    return 1\n", encoding="utf-8")
    diff = _diff_for("    completely_absent_line()", "    other()")
    line, _, method = resolve_mutant_line(
        src, "s.x_f__mutmut_1", diff, removed_line="    completely_absent_line()",
    )
    assert line is None
    assert method == "unresolved"


def test_method_inside_class_resolves(tmp_path):
    src = tmp_path / "rl.py"
    src.write_text(
        "import time\n"
        "\n"
        "\n"
        "class RateLimiter:\n"
        "    def __init__(self, limit):\n"
        "        self.limit = limit\n"
        "\n"
        "    def allow(self, n):\n"
        "        return n < self.limit\n",
        encoding="utf-8",
    )
    idx = build_function_index(src.read_text(encoding="utf-8"))
    assert "RateLimiter.allow" in idx
    assert idx["RateLimiter.allow"].start == 8

    mid = f"rl.x{CLASS_NAME_SEPARATOR}RateLimiter{CLASS_NAME_SEPARATOR}allow__mutmut_1"
    diff = _diff_for("        return n < self.limit", "        return n <= self.limit")
    line, ref, _ = resolve_mutant_line(
        src, mid, diff, removed_line="        return n < self.limit",
    )
    assert ref.qualified == "RateLimiter.allow"
    assert line == 9


def test_decorated_function_span_includes_decorator(tmp_path):
    src = tmp_path / "d.py"
    src.write_text(
        "class C:\n"
        "    @property\n"
        "    def value(self):\n"
        "        return self._v > 0\n",
        encoding="utf-8",
    )
    idx = build_function_index(src.read_text(encoding="utf-8"))
    assert idx["C.value"].start == 2          # the decorator line
    assert idx["C.value"].body_start == 4
