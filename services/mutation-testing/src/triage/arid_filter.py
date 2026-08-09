"""
Equivalent-Mutant Triage -- Tier 0 (static/arid detection)
==============================================================
Cheap, no-ML, no-training-data-needed filter for mutants that are
"uninteresting" -- either logically equivalent to the original program,
or mutating something with no behavioural consequence a test could ever
catch (a log message, a docstring, a __repr__ string).

IMPORTANT: this NEVER deletes a mutant. It tags it with a status +
reason_code, so it stays auditable -- Governance needs the count, and a
"trustworthy" framework shouldn't silently drop evidence (see ADR-001).

This is Tier 0 only. Tier 1 (bytecode-normalization) and Tier 2 (dynamic
differential execution) are NOT built yet -- flagged as a future step,
not faked here.
"""
import re
from dataclasses import dataclass


@dataclass
class TriageResult:
    status: str          # "active" | "equivalent_suspected"
    confidence: float
    reason_code: str


ARID_PATTERNS = [
    (re.compile(r"logging\.\w+\("), "logging_call"),
    (re.compile(r"__repr__"), "repr_method"),
    (re.compile(r'"""|\'\'\''), "docstring"),
]


def _is_string_literal_only_change(diff_text: str) -> bool:
    """Catches mutations that ONLY change a string literal's contents
    (e.g. an error message), where the surrounding code structure is
    identical. These can't be caught by any test that doesn't assert on
    exact message text, and in practice almost none do."""
    removed = [l[1:] for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++")]
    if len(removed) != 1 or len(added) != 1:
        return False
    # Strip string literal contents, compare the "skeleton" of the line
    skeleton_re = re.compile(r'"[^"]*"|\'[^\']*\'')
    rem_skeleton = skeleton_re.sub("STR", removed[0])
    add_skeleton = skeleton_re.sub("STR", added[0])
    return rem_skeleton == add_skeleton and removed[0] != added[0]


def triage_mutant(diff_text: str) -> TriageResult:
    for pattern, reason in ARID_PATTERNS:
        if pattern.search(diff_text):
            return TriageResult(status="equivalent_suspected", confidence=0.8, reason_code=reason)

    if _is_string_literal_only_change(diff_text):
        return TriageResult(status="equivalent_suspected", confidence=0.7, reason_code="string_literal_only")

    return TriageResult(status="active", confidence=1.0, reason_code="no_arid_pattern_matched")
