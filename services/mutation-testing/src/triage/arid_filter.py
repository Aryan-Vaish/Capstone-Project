"""
Equivalent-Mutant Triage -- Tier 0 (static / arid detection)
================================================================
Cheap, no-ML filter for mutants that are uninteresting -- either logically
equivalent to the original program, or mutating something with no behavioural
consequence any reasonable test could catch (a log message, a __repr__ string).

IMPORTANT: this NEVER deletes a mutant. It tags it with a status, a confidence
and a reason code, all three of which are now persisted to the database and
written into the per-run report. Governance needs the count, and a project whose
premise is trustworthiness cannot silently drop evidence. (v1 computed the
confidence and reason and then discarded them at the call site, keeping only the
status -- the promise was in the docstring but not in the data.)

Tier 0 only. Tier 1 (bytecode normalisation) and Tier 2 (dynamic differential
execution) are not built; they are named as future work rather than faked.

TWO FALSE-POSITIVE SOURCES FIXED
--------------------------------
1. v1 matched its patterns against the WHOLE diff text, context lines included.
   A single `logging.info(...)` anywhere in the surrounding context therefore
   flagged every mutant in that function as equivalent. Matching now looks only
   at the lines that actually changed.

2. v1 carried a `docstring` pattern, but mutmut never mutates docstrings --
   `operator_string` returns early for triple-quoted strings (mutators.py:40-43,
   "we assume triple-quoted stuff are docs"). That pattern could only ever fire
   on a context line, so it was a pure false-positive generator. Removed.

Enclosing-function checks (`__repr__`, `__str__`) now come from the caller's
parsed function name rather than from grepping the diff, so they catch mutations
in the method BODY -- which is where they actually occur -- instead of only
matching when the `def` line happens to be in the context window.
"""
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Methods whose entire purpose is producing a human-readable string. A mutation
# inside one is real, but no reasonable test suite asserts on it.
COSMETIC_METHODS = {"__repr__", "__str__", "__format__"}

# Patterns checked against CHANGED lines only, never context.
ARID_LINE_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\blog(?:ger|ging)?\.\w+\("), "logging_call"),
    (re.compile(r"\bwarnings\.warn\("), "warning_call"),
    (re.compile(r"\bprint\("), "print_call"),
]

_STRING_LITERAL_RE = re.compile(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'|"[^"]*"|\'[^\']*\'')


@dataclass
class TriageResult:
    status: str          # "active" | "equivalent_suspected"
    confidence: float
    reason_code: str


def _changed_lines(diff_text: str) -> Tuple[List[str], List[str]]:
    removed = [l[1:] for l in diff_text.splitlines()
               if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff_text.splitlines()
             if l.startswith("+") and not l.startswith("+++")]
    return removed, added


def _is_string_literal_only_change(removed: List[str], added: List[str]) -> bool:
    """True when the only difference is the CONTENT of a string literal.

    Mutations of this shape (error messages, log text) cannot be caught by any
    test that does not assert on exact message text, and in practice almost none
    do. Compares the "skeleton" of each line with literals blanked out.
    """
    if len(removed) != 1 or len(added) != 1:
        return False
    rem, add = removed[0], added[0]
    if rem == add:
        return False
    return _STRING_LITERAL_RE.sub("STR", rem) == _STRING_LITERAL_RE.sub("STR", add)


def triage_mutant(diff_text: str, function_name: Optional[str] = None,
                  operator: Optional[str] = None) -> TriageResult:
    """Classify one mutant as an active survivor or a suspected equivalent.

    `function_name` should be the bare name of the enclosing function, which the
    generator already parses out of the mutant id.
    """
    if function_name and function_name.split(".")[-1] in COSMETIC_METHODS:
        return TriageResult("equivalent_suspected", 0.9, "cosmetic_method")

    removed, added = _changed_lines(diff_text)
    changed = removed + added

    for pattern, reason in ARID_LINE_PATTERNS:
        if any(pattern.search(line) for line in changed):
            return TriageResult("equivalent_suspected", 0.8, reason)

    if _is_string_literal_only_change(removed, added):
        return TriageResult("equivalent_suspected", 0.7, "string_literal_only")

    return TriageResult("active", 1.0, "no_arid_pattern_matched")
