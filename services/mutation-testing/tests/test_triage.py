"""
Tier-0 equivalence triage tests.

THE REGRESSION THIS GUARDS
--------------------------
The first real collection run (670 mutants) failed the tier-validation gate for
6 of 10 corpus modules. The cause was this filter: its string-literal rule
blanked out every literal and compared line "skeletons", so ANY single-line
string-content change was called equivalent -- regardless of whether the string
was a log message or a dictionary key.

Measured against real corpus mutations, 6 of 7 flags were wrong. Data strings
(`STOP_WORDS` members, `split(" ")`, dict-dispatch keys, `return "A"`) are among
the MOST killable mutants in a corpus, not the least.

This matters beyond the counts: `mutation_score_adjusted` subtracts suspected
equivalents from its denominator, so an over-flagging filter launders genuine
test-suite failures into an uncounted bucket.

The cases below are drawn verbatim from the corpus modules that failed.
"""
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from triage.arid_filter import triage_mutant  # noqa: E402


def diff(before: str, after: str) -> str:
    """A mutmut-shaped unified diff around one changed line."""
    return "\n".join(["--- m.py", "+++ m.py", "@@ -1,3 +1,3 @@",
                      "-" + before, "+" + after])


# --------------------------------------------------------------------------
# Data strings must be ACTIVE -- these are the false positives that caused the
# gate failure. Each is a real mutation from the named corpus module.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module,before,after,why", [
    ("04_grade_calculator",
     '        return "A"', '        return "XXAXX"',
     "the weak suite literally asserts letter_grade(90) == 'A'"),
    ("04_grade_calculator",
     '    "A": 4.0,', '    "XXAXX": 4.0,',
     "dict key -- grade_points('A') would raise KeyError"),
    ("06_text_tokenizer",
     '    STOP_WORDS = {"the"}', '    STOP_WORDS = {"XXtheXX"}',
     "changes which words the tokenizer drops"),
    ("06_text_tokenizer",
     '    for raw in text.split(" "):', '    for raw in text.split("XX XX"):',
     "changes every single tokenizer result"),
    ("06_text_tokenizer",
     '    PUNCTUATION = ".,;:!?"', '    PUNCTUATION = "XX.,;:!?XX"',
     "changes which characters get stripped"),
    ("07_permission_flags",
     '    (("read", READ),)', '    (("XXreadXX", READ),)',
     "describe() returns this string"),
    ("10_order_state_machine",
     '    "new": ("paid",),', '    "XXnewXX": ("paid",),',
     "dict-dispatch key -- breaks every transition from 'new'"),
    ("10_order_state_machine",
     '    TERMINAL_STATES = {"delivered"}', '    TERMINAL_STATES = {"XXdeliveredXX"}',
     "changes is_terminal() for a real state"),
])
def test_data_strings_are_active_not_equivalent(module, before, after, why):
    result = triage_mutant(diff(before, after), function_name="some_function")
    assert result.status == "active", (
        f"{module}: {before.strip()!r} -> {after.strip()!r}\n"
        f"  flagged {result.status} ({result.reason_code}) but {why}"
    )


# --------------------------------------------------------------------------
# Message-position strings must still be flagged -- the rule was narrowed, not
# deleted. Losing these would send genuinely unkillable mutants to the ranker.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("before,after", [
    ('    raise ValueError("Insufficient funds")',
     '    raise ValueError("XXInsufficient fundsXX")'),
    ('    raise ValueError("Score must be between 0 and 100")',
     '    raise ValueError("score must be between 0 and 100")'),
    ('        raise KeyError("Unknown grade letter")',
     '        raise KeyError("XXUnknown grade letterXX")'),
    ('    logger.info("rate limit exceeded")',
     '    logger.info("XXrate limit exceededXX")'),
    ('        logger.debug("refilled tokens")',
     '        logger.debug("XXrefilled tokensXX")'),
    ('    logging.warning("deprecated")', '    logging.warning("XXdeprecatedXX")'),
    ('    print("hello")', '    print("XXhelloXX")'),
    ('    warnings.warn("old api")', '    warnings.warn("XXold apiXX")'),
])
def test_message_strings_remain_flagged(before, after):
    result = triage_mutant(diff(before, after), function_name="some_function")
    assert result.status == "low_value", (
        f"{before.strip()!r} should still be arid, got {result.reason_code}"
    )


def test_message_flag_uses_its_own_reason_code():
    """A distinct code so the narrowed rule is separable from the old one in
    already-collected data."""
    result = triage_mutant(diff(
        '    raise ValueError("nope")', '    raise ValueError("XXnopeXX")'))
    assert result.reason_code == "string_literal_message"
    assert result.confidence == 0.7


# --------------------------------------------------------------------------
# Rules that were already correct
# --------------------------------------------------------------------------

def test_cosmetic_method_flagged_by_enclosing_function():
    """__repr__ bodies are arid wherever the mutation lands, which is why this
    is keyed on the function name rather than on the diff text."""
    result = triage_mutant(
        diff('        return "RateLimiter({})".format(self.capacity)',
             '        return "RateLimiter({})".format(None)'),
        function_name="RateLimiter.__repr__",
    )
    assert result.status == "low_value"
    assert result.reason_code == "cosmetic_method"


def test_logging_call_line_flagged():
    result = triage_mutant(diff(
        '    logger.info("count=%s", n)', '    logger.info("count=%s", None)'))
    assert result.status == "low_value"
    assert result.reason_code == "logging_call"


def test_context_lines_do_not_trigger_flags():
    """A logging call in the surrounding CONTEXT must not flag an unrelated
    mutation -- the v1 bug that flagged every mutant in a logging function."""
    d = "\n".join([
        "--- m.py", "+++ m.py", "@@ -1,5 +1,5 @@",
        " def f(x):",
        '     logger.info("entering f")',
        "-    if x > 10:",
        "+    if x >= 10:",
        "     return x",
    ])
    result = triage_mutant(d, function_name="f")
    assert result.status == "active"


# --------------------------------------------------------------------------
# Non-string mutations are unaffected by the narrowing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("before,after", [
    ("    if amount > balance:", "    if amount >= balance:"),
    ("    return balance - amount", "    return balance + amount"),
    ("    count = 0", "    count = 1"),
    ("    if username is None:", "    if username is not None:"),
    ("    return flags & MASK", "    return flags | MASK"),
])
def test_non_string_mutations_stay_active(before, after):
    assert triage_mutant(diff(before, after)).status == "active"


def test_triage_result_carries_full_provenance():
    """status, confidence and reason_code are all persisted to the dataset --
    v1 computed all three and kept only the status."""
    result = triage_mutant(diff("    a = 1", "    a = 2"))
    assert result.status == "active"
    assert result.confidence == 1.0
    assert result.reason_code == "no_arid_pattern_matched"


def test_tier0_never_claims_equivalence():
    """Tier-0 rules are aridity judgments, not equivalence claims. The strong
    suite killed 32 of 39 flags in the v2 dataset, so these mutants are
    demonstrably killable. `equivalent_suspected` is reserved for Tier 1
    (bytecode) and Tier 2 (dynamic) detection, neither of which is built.
    See ADR-005."""
    import inspect
    from src.triage import arid_filter
    source = inspect.getsource(arid_filter)
    assert 'TriageResult("equivalent_suspected"' not in source
