"""
Templated Missing-Test-Case Explanation
==========================================
Turns a surviving mutant into a plain-English description of what test is
actually missing, using its operator type + AST context. This is
DELIBERATELY separate from attribution.py -- SHAP explains why the MODEL
ranked a mutant highly (a statement about the model), this explains what
the CODE is missing (a statement about the mutant's semantics). Conflating
the two was flagged as a scaffold problem early in this project: SHAP
cannot tell you what test is missing, only why the model scored something
the way it did.
"""
from typing import Dict, Optional

TEMPLATES = {
    "conditional_boundary": "No test exercises the exact boundary condition at {location} "
                              "(e.g. the case where the compared values are equal).",
    "negate_conditional": "No test covers the negated/opposite branch of the condition at {location}.",
    "arithmetic_replacement": "No test would catch an alternate arithmetic outcome at {location} -- "
                                "consider a test with a value that makes the two operators diverge.",
    "statement_deletion": "No test would fail if the statement at {location} were removed entirely.",
    "return_value_replacement": "No test asserts on the exact return value produced at {location}.",
    "null_check_removal": "No test covers a None/empty input reaching {location}.",
    "constant_replacement": "No test pins down the exact constant value used at {location}.",
    "unknown": "A mutation at {location} survived, but its exact semantic category wasn't "
                "recognized by our operator taxonomy (often a string-literal or message-only change).",
}

STUB_TEMPLATES = {
    "conditional_boundary": "def test_{fn}_boundary_condition():\n    # targets the boundary at {location}\n    ...\n",
    "negate_conditional": "def test_{fn}_negated_branch():\n    # targets the opposite branch at {location}\n    ...\n",
    "arithmetic_replacement": "def test_{fn}_arithmetic_edge_case():\n    # targets the calculation at {location}\n    ...\n",
    "statement_deletion": "def test_{fn}_statement_required():\n    # confirms the statement at {location} has an effect\n    ...\n",
    "return_value_replacement": "def test_{fn}_exact_return_value():\n    # asserts precise return value at {location}\n    ...\n",
    "null_check_removal": "def test_{fn}_none_input():\n    # targets None/empty input handling at {location}\n    ...\n",
    "constant_replacement": "def test_{fn}_constant_value():\n    # pins down the constant at {location}\n    ...\n",
    "unknown": "def test_{fn}_additional_case():\n    # investigate the mutation at {location} manually\n    ...\n",
}


def generate_missing_edge_case(operator: str, location: str) -> str:
    template = TEMPLATES.get(operator, TEMPLATES["unknown"])
    return template.format(location=location)


def generate_test_stub(operator: str, location: str, function_name: str = "target") -> str:
    template = STUB_TEMPLATES.get(operator, STUB_TEMPLATES["unknown"])
    return template.format(fn=function_name, location=location)


def summarize_survivor_patterns(survivor_operators: list) -> str:
    """Aggregates across all survivors for the overall explanation_summary
    field -- e.g. 'boundary and null-check mutants dominate survivors'."""
    if not survivor_operators:
        return "No active survivors -- the test suite killed or the pipeline filtered every mutant."
    from collections import Counter
    counts = Counter(survivor_operators)
    top = counts.most_common(2)
    parts = [f"{op.replace('_', ' ')} ({n})" for op, n in top]
    return f"Survivors are dominated by: {', '.join(parts)}."
