"""
Missing-Test Explanation (templated NLG)
==========================================
Turns a surviving mutant into a plain-English description of the test that is
missing, plus a pytest stub to start from.

This answers a DIFFERENT question from explainability/attribution.py, and the
two are deliberately kept apart:

    attribution.py (SHAP)  ->  why did the MODEL rank this mutant highly?
    test_gap.py (here)     ->  what TEST is actually missing?

The first is a statement about the model, the second about the code. A user
asking "what should I write?" wants the second. Conflating them would leave the
module unable to answer the question it exists to answer.

Everything here is deterministic template expansion keyed on the taxonomy-v2
operator. It needs no trained model -- a fact worth stating explicitly, because
v1 gated these calls behind `if _ML_MODEL` in main.py, so a fresh checkout with
no model.json produced `missing_edge_case: None` for every mutant even though
nothing in this file requires one.
"""
from collections import Counter
from typing import List

from mutators import taxonomy as tx

# What test is missing, keyed on what the mutation changed.
TEMPLATES = {
    tx.COMPARISON_BOUNDARY:
        "No test pins down the exact boundary at {location} -- the comparison can be "
        "loosened or tightened by one step without any test noticing. Add a case where "
        "the two compared values are exactly equal.",
    tx.EQUALITY_NEGATION:
        "No test distinguishes the equal case from the not-equal case at {location}. "
        "Add assertions for both a matching and a non-matching value.",
    tx.LOGICAL_CONNECTOR_SWAP:
        "The `and`/`or` at {location} can be swapped without failing any test, so only "
        "one side of the condition is ever exercised. Add a case where exactly one "
        "operand is true.",
    tx.ARITHMETIC_REPLACEMENT:
        "The arithmetic operator at {location} can be replaced without detection -- the "
        "tests check the shape of the result but never its exact value. Assert on a "
        "concrete computed number.",
    tx.BITWISE_REPLACEMENT:
        "The bitwise operator at {location} can be swapped undetected. Add a case with "
        "flag combinations that distinguish AND from OR (e.g. exactly one bit set).",
    tx.AUGMENTED_ASSIGN_SWAP:
        "The augmented assignment at {location} can be reversed without failing a test. "
        "Assert the accumulated value after more than one iteration.",
    tx.AUGMENTED_ASSIGN_REMOVAL:
        "The accumulation at {location} can be replaced by a plain assignment and no "
        "test notices -- so no test runs this line more than once with a non-zero prior "
        "value. Add a multi-iteration case.",
    tx.NUMERIC_CONSTANT_CHANGE:
        "The numeric constant at {location} can be changed by one without any test "
        "failing. Assert the exact expected value rather than a range or a type.",
    tx.STRING_LITERAL_CHANGE:
        "The string literal at {location} is never asserted on -- its content can be "
        "rewritten freely. If it is user-facing or a lookup key, assert its exact value.",
    tx.BOOLEAN_CONSTANT_FLIP:
        "The boolean literal at {location} can be inverted without detection. Add a test "
        "that asserts the returned/assigned truth value directly.",
    tx.NAME_REPLACEMENT:
        "The call at {location} can be swapped for a shallower equivalent without any "
        "test noticing. Add a case where the distinction matters (e.g. a nested mutation).",
    tx.ASSIGNMENT_TO_NONE:
        "The value assigned at {location} can be replaced with None and no test fails, "
        "so this variable's value never reaches an assertion. Assert on what it produces.",
    tx.UNARY_OPERATOR_REMOVAL:
        "The negation at {location} can be dropped without failing a test -- only one "
        "polarity of this condition is ever exercised. Add a case for the other.",
    tx.IDENTITY_NEGATION:
        "The identity check at {location} can be inverted undetected. Add cases for both "
        "the None (or identical-object) path and its opposite.",
    tx.MEMBERSHIP_NEGATION:
        "The membership test at {location} can be inverted without detection. Add both a "
        "present and an absent element.",
    tx.LOOP_CONTROL_SWAP:
        "The loop-control statement at {location} can be changed without failing a test, "
        "so no test depends on the loop stopping (or continuing) exactly here. Add a case "
        "with several matching elements.",
    tx.ARGUMENT_REPLACEMENT:
        "An argument in the call at {location} can be replaced with None undetected -- "
        "no test exercises a path where that argument's value changes the result.",
    tx.ARGUMENT_REMOVAL:
        "An argument can be dropped from the call at {location} without any test failing. "
        "Assert on behaviour that depends on that argument.",
    tx.STRING_METHOD_SWAP:
        "The string method at {location} can be swapped for its mirror (e.g. lower/upper, "
        "lstrip/rstrip) undetected. Add input where the two differ.",
    tx.DICT_KEYWORD_CHANGE:
        "A dict key built at {location} can be renamed without any test failing -- no "
        "test reads that key back. Assert on the resulting dictionary's contents.",
    tx.LAMBDA_BODY_REPLACEMENT:
        "The lambda at {location} can be replaced with one returning None, so its result "
        "is never asserted. Add a case that depends on the lambda's actual output "
        "(e.g. a sort order).",
    tx.MATCH_CASE_REMOVAL:
        "A `case` arm at {location} can be deleted without failing a test -- that branch "
        "is never taken. Add an input that routes to it.",
    tx.OTHER:
        "A mutation at {location} survived the test suite, but its shape was not "
        "recognised by the operator taxonomy. Inspect the diff and add a test that "
        "distinguishes the original behaviour from the mutated one.",
}

# A starting-point pytest stub. Names are intentionally descriptive rather than
# minimal -- the stub is meant to be pasted into a real suite and filled in.
STUB_TEMPLATES = {
    tx.COMPARISON_BOUNDARY:
        "def test_{fn}_at_exact_boundary():\n"
        "    # {location}: exercise the equality case of the comparison\n"
        "    ...",
    tx.EQUALITY_NEGATION:
        "def test_{fn}_equal_and_not_equal():\n"
        "    # {location}: one matching value, one non-matching\n"
        "    ...",
    tx.LOGICAL_CONNECTOR_SWAP:
        "def test_{fn}_each_condition_independently():\n"
        "    # {location}: exactly one operand true, then the other\n"
        "    ...",
    tx.ARITHMETIC_REPLACEMENT:
        "def test_{fn}_exact_computed_value():\n"
        "    # {location}: assert the precise number, not just its type or sign\n"
        "    ...",
    tx.BITWISE_REPLACEMENT:
        "def test_{fn}_distinguishes_bitwise_ops():\n"
        "    # {location}: flags where AND and OR give different results\n"
        "    ...",
    tx.AUGMENTED_ASSIGN_SWAP:
        "def test_{fn}_accumulates_correctly():\n"
        "    # {location}: assert the value after several updates\n"
        "    ...",
    tx.AUGMENTED_ASSIGN_REMOVAL:
        "def test_{fn}_accumulates_over_multiple_iterations():\n"
        "    # {location}: needs more than one iteration to be observable\n"
        "    ...",
    tx.NUMERIC_CONSTANT_CHANGE:
        "def test_{fn}_uses_exact_constant():\n"
        "    # {location}: assert the exact expected value\n"
        "    ...",
    tx.STRING_LITERAL_CHANGE:
        "def test_{fn}_exact_string_content():\n"
        "    # {location}: assert the literal text\n"
        "    ...",
    tx.BOOLEAN_CONSTANT_FLIP:
        "def test_{fn}_returns_expected_boolean():\n"
        "    # {location}: assert True/False explicitly\n"
        "    ...",
    tx.NAME_REPLACEMENT:
        "def test_{fn}_copy_semantics():\n"
        "    # {location}: nested value must not be shared after the call\n"
        "    ...",
    tx.ASSIGNMENT_TO_NONE:
        "def test_{fn}_intermediate_value_is_used():\n"
        "    # {location}: assert on the result this variable feeds\n"
        "    ...",
    tx.UNARY_OPERATOR_REMOVAL:
        "def test_{fn}_both_polarities():\n"
        "    # {location}: the condition true, and false\n"
        "    ...",
    tx.IDENTITY_NEGATION:
        "def test_{fn}_none_and_not_none():\n"
        "    # {location}: both sides of the identity check\n"
        "    ...",
    tx.MEMBERSHIP_NEGATION:
        "def test_{fn}_present_and_absent():\n"
        "    # {location}: an element in the collection, and one not in it\n"
        "    ...",
    tx.LOOP_CONTROL_SWAP:
        "def test_{fn}_stops_at_first_match():\n"
        "    # {location}: several matching elements, assert only the first counts\n"
        "    ...",
    tx.ARGUMENT_REPLACEMENT:
        "def test_{fn}_argument_affects_result():\n"
        "    # {location}: vary that argument, assert the result changes\n"
        "    ...",
    tx.ARGUMENT_REMOVAL:
        "def test_{fn}_requires_every_argument():\n"
        "    # {location}: behaviour must depend on each argument passed\n"
        "    ...",
    tx.STRING_METHOD_SWAP:
        "def test_{fn}_string_transform_direction():\n"
        "    # {location}: input where lower/upper (or l/r variants) differ\n"
        "    ...",
    tx.DICT_KEYWORD_CHANGE:
        "def test_{fn}_dict_keys():\n"
        "    # {location}: assert the exact keys produced\n"
        "    ...",
    tx.LAMBDA_BODY_REPLACEMENT:
        "def test_{fn}_lambda_result_matters():\n"
        "    # {location}: assert the ordering/result the lambda produces\n"
        "    ...",
    tx.MATCH_CASE_REMOVAL:
        "def test_{fn}_routes_to_every_case():\n"
        "    # {location}: an input for each match arm\n"
        "    ...",
    tx.OTHER:
        "def test_{fn}_unrecognised_mutation():\n"
        "    # {location}: inspect the mutant diff and assert the difference\n"
        "    ...",
}


def generate_missing_edge_case(operator: str, location: str) -> str:
    template = TEMPLATES.get(operator, TEMPLATES[tx.OTHER])
    return template.format(location=location)


def generate_test_stub(operator: str, location: str, function_name: str = "target") -> str:
    template = STUB_TEMPLATES.get(operator, STUB_TEMPLATES[tx.OTHER])
    # a qualified name like "RateLimiter.allow" is not a valid identifier fragment
    safe_fn = function_name.replace(".", "_")
    return template.format(fn=safe_fn, location=location)


def summarize_survivor_patterns(survivor_operators: List[str], top_n: int = 2) -> str:
    """One-line summary of what kind of gaps dominate this run."""
    if not survivor_operators:
        return ""
    counts = Counter(survivor_operators).most_common(top_n)
    parts = [f"{name.replace('_', ' ')} ({n})" for name, n in counts]
    return "Most common surviving mutation types: " + ", ".join(parts) + "."
