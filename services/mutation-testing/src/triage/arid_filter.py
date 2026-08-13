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

THREE FALSE-POSITIVE SOURCES FIXED
----------------------------------
1. v1 matched its patterns against the WHOLE diff text, context lines included.
   A single `logging.info(...)` anywhere in the surrounding context therefore
   flagged every mutant in that function as equivalent. Matching now looks only
   at the lines that actually changed.

2. v1 carried a `docstring` pattern, but mutmut never mutates docstrings --
   `operator_string` returns early for triple-quoted strings (mutators.py:40-43,
   "we assume triple-quoted stuff are docs"). That pattern could only ever fire
   on a context line, so it was a pure false-positive generator. Removed.

3. THE BIG ONE. The string-literal rule blanked out every literal and compared
   the resulting line "skeletons", so ANY single-line string-content change
   matched -- with no regard for whether the string was a log message or a
   dictionary key. Measured against real corpus mutations, 6 of 7 flags were
   wrong:

       text.split(" ")   -> split("XX XX")   changes every tokenizer result
       STOP_WORDS {"the"} -> {"XXtheXX"}     changes tokenizer output
       "new": ("paid",)  -> "XXnewXX"        breaks state-machine dispatch
       return "A"        -> return "XXAXX"   module 04's suite asserts on this
       raise ValueError("Bad score")         <- only this one was truly arid

   Data strings are among the MOST killable mutants in a corpus, not the least.
   Mislabelling them matters beyond the counts: `mutation_score_adjusted`
   subtracts suspected-equivalents from its denominator, so an over-flagging
   filter launders genuine test-suite failures into an uncounted bucket -- the
   exact inflation this project exists to avoid.

   The rule is now inverted: a string change is ACTIVE unless the literal is
   positively identified as sitting in MESSAGE POSITION (an argument to raise /
   logging / warnings.warn / print), or the enclosing function is cosmetic.
   Erring toward reporting a mutant is safe; erring toward hiding one is what
   produced this bug.

Enclosing-function checks (`__repr__`, `__str__`) come from the caller's parsed
function name rather than from grepping the diff, so they catch mutations in the
method BODY -- where they actually occur -- instead of only matching when the
`def` line happens to sit in the context window.
"""
import ast
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

    Necessary but NOT sufficient for aridity -- see `_is_message_position`.
    """
    if len(removed) != 1 or len(added) != 1:
        return False
    rem, add = removed[0], added[0]
    if rem == add:
        return False
    return _STRING_LITERAL_RE.sub("STR", rem) == _STRING_LITERAL_RE.sub("STR", add)


# Callables whose string arguments exist to be read by humans, not compared by
# programs. A mutation inside one is real but unobservable to any reasonable
# test. Matched on the attribute name, so `log.info`, `logger.warning` and
# `self._log.debug` all qualify.
_MESSAGE_CALL_NAMES = {
    "debug", "info", "warning", "warn", "error", "exception", "critical", "log",
    "print",
}
_MESSAGE_CALL_ROOTS = {"logging", "logger", "log", "warnings", "sys"}


def _call_is_message_sink(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in {"print"} or func.id in _MESSAGE_CALL_NAMES
    if isinstance(func, ast.Attribute):
        if func.attr in _MESSAGE_CALL_NAMES:
            return True
        root = func.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in _MESSAGE_CALL_ROOTS:
            return True
    return False


def _is_message_position(line: str) -> bool:
    """Does this line's string literal sit where only a human would read it?

    Positively identifies the arid cases -- a `raise Exc("...")`, a logging or
    warning call, a `print`. Everything else (dict keys, set members, comparison
    operands, `split(" ")`, `return "A"`) falls through to ACTIVE.

    A line that cannot be parsed standalone -- `"A": 4.0,` inside a dict
    literal, a tuple element, a continuation -- returns False, which is the
    correct answer for every such construct we have seen: those are data
    strings, and data strings are killable.
    """
    stripped = line.strip()
    if not stripped:
        return False
    try:
        tree = ast.parse(stripped)
    except SyntaxError:
        return False

    for node in ast.walk(tree):
        # raise ValueError("...") / raise ValueError(f"...")
        if isinstance(node, ast.Raise):
            if any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   for n in ast.walk(node)):
                return True
        if isinstance(node, ast.Call) and _call_is_message_sink(node):
            if any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                   for n in ast.walk(node)):
                return True
    return False


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

    # A string-content change is arid ONLY when the literal is in message
    # position. Requiring both conditions is the fix for the over-flagging
    # described in the module docstring: the skeleton comparison alone matched
    # dict keys, set members and return values, which are highly killable.
    if _is_string_literal_only_change(removed, added):
        if _is_message_position(removed[0]):
            return TriageResult("equivalent_suspected", 0.7, "string_literal_message")
        # Deliberately falls through to active: a data string that changes
        # behaviour is a real survivor, and hiding it would inflate the score.

    return TriageResult("active", 1.0, "no_arid_pattern_matched")
