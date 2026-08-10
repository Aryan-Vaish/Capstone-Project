"""
Mutation Operator Classifier (taxonomy v2)
=============================================
Turns a `mutmut show` unified diff into one taxonomy-v2 operator name.

Taxonomy v1 classified 73.5% of real mutants as `unknown`. The cause was
structural, not a matter of missing rules: it joined *all* removed lines into a
single string and *all* added lines into another, then ran ordered regexes over
the pair. A `-` appearing anywhere in the removed block therefore matched the
arithmetic rule regardless of what actually changed, and any mutation whose
signature was not a bare operator character was unreachable.

This module instead lexes the changed lines with `tokenize` and aligns the two
token streams with `difflib`, so classification is driven by the *minimal token
substitution* that mutmut actually made. That is the same shape as mutmut's own
model -- it mutates one CST node at a time (see
docs/Research/experiments/mutmut_3.7_operator_catalogue.md) -- so a token-level
diff recovers the operator almost exactly.

Everything here is pure Python over recorded diff text: no mutmut, no
subprocess, no WSL. It is therefore fully unit-testable on any platform, which
is the point -- the classifier is the component most likely to need iteration.
"""
import difflib
import io
import re
import token as token_mod
import tokenize
from dataclasses import dataclass
from typing import List, Optional, Tuple

from mutators.taxonomy import (
    ARGUMENT_REMOVAL,
    ARGUMENT_REPLACEMENT,
    ARITHMETIC_REPLACEMENT,
    ASSIGNMENT_TO_NONE,
    AUGMENTED_ASSIGN_REMOVAL,
    AUGMENTED_ASSIGN_SWAP,
    BITWISE_REPLACEMENT,
    BOOLEAN_CONSTANT_FLIP,
    COMPARISON_BOUNDARY,
    DICT_KEYWORD_CHANGE,
    EQUALITY_NEGATION,
    IDENTITY_NEGATION,
    LAMBDA_BODY_REPLACEMENT,
    LOGICAL_CONNECTOR_SWAP,
    LOOP_CONTROL_SWAP,
    MATCH_CASE_REMOVAL,
    MEMBERSHIP_NEGATION,
    NAME_REPLACEMENT,
    NUMERIC_CONSTANT_CHANGE,
    OTHER,
    STRING_LITERAL_CHANGE,
    STRING_METHOD_SWAP,
    UNARY_OPERATOR_REMOVAL,
)


@dataclass
class Classification:
    """Result of classifying one diff.

    `removed_line` is what the line-number resolver searches for in the original
    source, so it is returned here rather than re-parsed by the caller.
    """
    operator: str
    removed_line: Optional[str] = None
    added_line: Optional[str] = None
    detail: str = ""          # the token substitution, for the unclassified log


# --- exact token-pair substitutions, straight from mutmut's _operator_mapping ---

_ARITHMETIC = {"+", "-", "*", "/", "//", "%", "**"}
_BITWISE = {"&", "|", "^", "<<", ">>"}
_AUG_ARITHMETIC = {"+=", "-=", "*=", "/=", "//=", "%=", "**="}
_AUG_BITWISE = {"&=", "|=", "^=", "<<=", ">>="}

_COMPARISON_PAIRS = {("<", "<="), ("<=", "<"), (">", ">="), (">=", ">")}
_EQUALITY_PAIRS = {("==", "!="), ("!=", "==")}
_LOGICAL_PAIRS = {("and", "or"), ("or", "and")}
_LOOP_CONTROL_PAIRS = {("break", "return"), ("continue", "break")}
_NAME_PAIRS = {("deepcopy", "copy")}

# mutators.py:100-117 -- both directions are registered by mutmut itself
_STRING_METHOD_PAIRS = {
    ("lower", "upper"), ("upper", "lower"),
    ("lstrip", "rstrip"), ("rstrip", "lstrip"),
    ("find", "rfind"), ("rfind", "find"),
    ("ljust", "rjust"), ("rjust", "ljust"),
    ("index", "rindex"), ("rindex", "index"),
    ("removeprefix", "removesuffix"), ("removesuffix", "removeprefix"),
    ("partition", "rpartition"), ("rpartition", "partition"),
    ("split", "rsplit"), ("rsplit", "split"),
}

_STRING_QUOTE_RE = re.compile(r"""^([a-zA-Z]*)('''|\"\"\"|'|")(.*)(\2)$""", re.DOTALL)


def _tokenize(line: str) -> Optional[List[Tuple[int, str]]]:
    """Lex one source line into (token_type, text), dropping layout tokens.

    Returns None when the line cannot stand alone -- a continuation line inside
    an unclosed bracket, for instance. Callers treat that as "fall back to a
    coarser signal" rather than as an error, since mutmut does emit multi-line
    function signatures.
    """
    stripped = line.strip()
    if not stripped:
        return None
    skip = {
        token_mod.NEWLINE, token_mod.NL, token_mod.INDENT,
        token_mod.DEDENT, token_mod.ENDMARKER, token_mod.COMMENT,
    }
    out: List[Tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(stripped).readline):
            if tok.type in skip:
                continue
            out.append((tok.type, tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return out


def _strip_string_quotes(literal: str) -> Optional[str]:
    """Inner text of a string literal, or None if it does not look like one."""
    m = _STRING_QUOTE_RE.match(literal.strip())
    return m.group(3) if m else None


def _is_number_increment(old: str, new: str) -> bool:
    """mutmut's operator_number yields repr(value + 1); confirm rather than assume."""
    try:
        return complex(new) == complex(old) + 1
    except (ValueError, TypeError):
        return False


def _classify_replacement(old: List[str], new: List[str],
                          old_types: List[int], new_types: List[int]) -> Optional[str]:
    """Classify a token span that was replaced by another token span."""
    # --- single token -> single token: the common case ---
    if len(old) == 1 and len(new) == 1:
        o, n = old[0], new[0]
        pair = (o, n)

        if pair in _COMPARISON_PAIRS:
            return COMPARISON_BOUNDARY
        if pair in _EQUALITY_PAIRS:
            return EQUALITY_NEGATION
        if pair in _LOGICAL_PAIRS:
            return LOGICAL_CONNECTOR_SWAP
        if pair in _LOOP_CONTROL_PAIRS:
            return LOOP_CONTROL_SWAP
        if pair in _NAME_PAIRS:
            return NAME_REPLACEMENT
        if pair in _STRING_METHOD_PAIRS:
            return STRING_METHOD_SWAP
        if {o, n} == {"True", "False"}:
            return BOOLEAN_CONSTANT_FLIP

        # augmented assignment: swapped operator vs. collapsed to plain `=`
        if o in (_AUG_ARITHMETIC | _AUG_BITWISE):
            if n == "=":
                return AUGMENTED_ASSIGN_REMOVAL
            if n in (_AUG_ARITHMETIC | _AUG_BITWISE):
                return AUGMENTED_ASSIGN_SWAP

        if o in _ARITHMETIC and n in _ARITHMETIC:
            return ARITHMETIC_REPLACEMENT
        if o in _BITWISE and n in _BITWISE:
            return BITWISE_REPLACEMENT

        # numeric literal n -> n+1
        if old_types[0] == token_mod.NUMBER and new_types[0] == token_mod.NUMBER:
            return NUMERIC_CONSTANT_CHANGE if _is_number_increment(o, n) else NUMERIC_CONSTANT_CHANGE

        # string literal: XX-wrap or case swap (mutators.py:45-50)
        if old_types[0] == token_mod.STRING and new_types[0] == token_mod.STRING:
            oi, ni = _strip_string_quotes(o), _strip_string_quotes(n)
            if oi is not None and ni is not None:
                if ni == f"XX{oi}XX" or ni == oi.lower() or ni == oi.upper():
                    return STRING_LITERAL_CHANGE
            return STRING_LITERAL_CHANGE

        # dict(a=1) -> dict(aXX=1)
        if old_types[0] == token_mod.NAME and new_types[0] == token_mod.NAME and n == f"{o}XX":
            return DICT_KEYWORD_CHANGE

        # a single argument replaced by None (operator_arg_removal, first branch)
        if n == "None":
            return ARGUMENT_REPLACEMENT

    # --- many tokens -> `None` / `""`: whole RHS or lambda body collapsed ---
    if len(new) == 1 and new[0] in ("None", '""', "''"):
        return None  # decided by the caller, which can see `=` / `lambda` context
    if len(new) == 1 and new[0] == "0" and len(old) == 1 and old[0] == "None":
        return LAMBDA_BODY_REPLACEMENT  # mutators.py:62-63

    return None


def _find_context(tokens: List[str], start: int) -> str:
    """What syntactic position does index `start` sit in? Used only to
    disambiguate the several mutations that all collapse something to `None`."""
    before = tokens[:start]
    if "lambda" in before:
        return "lambda"
    # an unclosed '(' before the change means we are inside a call's argument list
    depth = before.count("(") - before.count(")")
    if depth > 0:
        return "call"
    if "=" in before:
        return "assign"
    return ""


def classify_diff(diff_text: str) -> Classification:
    """Classify one `mutmut show` diff into a taxonomy-v2 operator."""
    removed = [l[1:] for l in diff_text.splitlines()
               if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in diff_text.splitlines()
             if l.startswith("+") and not l.startswith("+++")]

    r_line = removed[0] if removed else None
    a_line = added[0] if added else None

    # A `match` arm dropped whole, or any other pure deletion of complete lines.
    if removed and not added:
        joined = " ".join(x.strip() for x in removed)
        if joined.startswith("case "):
            return Classification(MATCH_CASE_REMOVAL, r_line, None, "case arm removed")
        return Classification(OTHER, r_line, None, f"deletion: {joined[:80]}")

    if not removed or not added:
        return Classification(OTHER, r_line, a_line, "no paired change")

    # mutmut mutates one node, so the interesting case is one line -> one line.
    # Multi-line hunks are real (multi-line signatures, wrapped expressions);
    # pair them up positionally and classify the first line that differs.
    pairs = list(zip(removed, added))
    if len(removed) != len(added):
        pairs = [(removed[0], added[0])]

    for old_line, new_line in pairs:
        if old_line.strip() == new_line.strip():
            continue

        old_toks = _tokenize(old_line)
        new_toks = _tokenize(new_line)
        if old_toks is None or new_toks is None:
            # Un-lexable (continuation line). Fall back to a whole-line signal
            # rather than guessing an operator from partial text.
            return Classification(OTHER, old_line, new_line, "line did not lex standalone")

        old_str = [t[1] for t in old_toks]
        new_str = [t[1] for t in new_toks]
        old_typ = [t[0] for t in old_toks]
        new_typ = [t[0] for t in new_toks]

        matcher = difflib.SequenceMatcher(a=old_str, b=new_str, autojunk=False)
        ops = [op for op in matcher.get_opcodes() if op[0] != "equal"]
        if not ops:
            continue

        tag, i1, i2, j1, j2 = ops[0]
        o_span, n_span = old_str[i1:i2], new_str[j1:j2]
        detail = f"{tag}: {' '.join(o_span)!r} -> {' '.join(n_span)!r}"

        if tag == "replace":
            result = _classify_replacement(o_span, n_span, old_typ[i1:i2], new_typ[j1:j2])
            if result:
                return Classification(result, old_line, new_line, detail)

            # `<something> -> None` is ambiguous without context: it is an
            # argument replacement inside a call, a lambda body replacement
            # inside a lambda, and an assignment-to-None after `=`.
            if n_span == ["None"] or n_span in (['""'], ["''"]):
                ctx = _find_context(old_str, i1)
                if ctx == "lambda":
                    return Classification(LAMBDA_BODY_REPLACEMENT, old_line, new_line, detail)
                if ctx == "call":
                    return Classification(ARGUMENT_REPLACEMENT, old_line, new_line, detail)
                if ctx == "assign":
                    return Classification(ASSIGNMENT_TO_NONE, old_line, new_line, detail)
                return Classification(ASSIGNMENT_TO_NONE, old_line, new_line, detail)

            return Classification(OTHER, old_line, new_line, detail)

        if tag == "insert":
            # `is` -> `is not`, `in` -> `not in`
            if n_span == ["not"]:
                prev = new_str[j1 - 1] if j1 > 0 else ""
                nxt = new_str[j2] if j2 < len(new_str) else ""
                if prev == "is":
                    return Classification(IDENTITY_NEGATION, old_line, new_line, detail)
                if nxt == "in":
                    return Classification(MEMBERSHIP_NEGATION, old_line, new_line, detail)
            return Classification(OTHER, old_line, new_line, detail)

        if tag == "delete":
            if o_span == ["not"]:
                prev = old_str[i1 - 1] if i1 > 0 else ""
                nxt = old_str[i2] if i2 < len(old_str) else ""
                if prev == "is":
                    return Classification(IDENTITY_NEGATION, old_line, new_line, detail)
                if nxt == "in":
                    return Classification(MEMBERSHIP_NEGATION, old_line, new_line, detail)
                return Classification(UNARY_OPERATOR_REMOVAL, old_line, new_line, detail)
            if o_span == ["~"]:
                return Classification(UNARY_OPERATOR_REMOVAL, old_line, new_line, detail)
            # a dropped call argument leaves behind its trailing comma
            if _find_context(old_str, i1) == "call":
                return Classification(ARGUMENT_REMOVAL, old_line, new_line, detail)
            return Classification(OTHER, old_line, new_line, detail)

    return Classification(OTHER, r_line, a_line, "no differing line found")
