"""
Explainability tests.

Two separate systems, tested separately because they answer different questions:
  * test_gap.py     -> what test is missing?   (deterministic templates)
  * attribution.py  -> why did the model rank this highly?  (SHAP)

The templated system must work with NO trained model. v1 gated it behind
`if _ML_MODEL` in main.py, so a fresh checkout produced no explanations at all.
"""
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from explainability.attribution import explain_prediction, top_contributing_feature  # noqa: E402
from explainability.test_gap import (  # noqa: E402
    STUB_TEMPLATES,
    TEMPLATES,
    generate_missing_edge_case,
    generate_test_stub,
    summarize_survivor_patterns,
)
from ml.prioritizer import load_model  # noqa: E402
from mutators import taxonomy as tx  # noqa: E402


# --------------------------------------------------------------------------
# taxonomy / template coverage -- guards against silent drift
# --------------------------------------------------------------------------

def test_every_operator_has_both_templates():
    """A taxonomy edit must not leave an operator without an explanation.
    v1 kept the category list in ml/prioritizer.py and the templates in
    test_gap.py with nothing tying them together."""
    assert set(TEMPLATES) == set(tx.OPERATORS)
    assert set(STUB_TEMPLATES) == set(tx.OPERATORS)


def test_no_template_references_a_removed_operator():
    """statement_deletion and return_value_replacement cannot be produced by
    mutmut at all (see docs/Research/experiments/mutmut_3.7_operator_catalogue.md
    section 3) -- they were dead one-hot columns in v1."""
    assert "statement_deletion" not in tx.OPERATORS
    assert "return_value_replacement" not in tx.OPERATORS


@pytest.mark.parametrize("operator", tx.OPERATORS)
def test_explanation_renders_for_every_operator(operator):
    text = generate_missing_edge_case(operator, "corpus/m.py:12")
    assert "corpus/m.py:12" in text
    assert "{" not in text, "unrendered format placeholder"
    assert len(text) > 30


@pytest.mark.parametrize("operator", tx.OPERATORS)
def test_stub_renders_and_is_valid_python(operator):
    stub = generate_test_stub(operator, "corpus/m.py:12", "withdraw")
    assert "withdraw" in stub
    assert "{" not in stub
    compile(stub, "<stub>", "exec")   # must be syntactically valid


def test_stub_uses_the_function_name_not_the_module_name():
    """v1 passed source_path.stem, producing `def test_module_...` for every
    module -- the mutant id already carried the real function name, unused."""
    stub = generate_test_stub(tx.COMPARISON_BOUNDARY, "corpus/m.py:3", "withdraw")
    assert "test_withdraw" in stub


def test_stub_handles_qualified_method_names():
    """`RateLimiter.allow` is not a legal identifier fragment."""
    stub = generate_test_stub(tx.COMPARISON_BOUNDARY, "corpus/m.py:9", "RateLimiter.allow")
    compile(stub, "<stub>", "exec")
    assert "RateLimiter_allow" in stub


def test_unknown_operator_falls_back_rather_than_raising():
    text = generate_missing_edge_case("an_operator_that_does_not_exist", "m.py:1")
    assert "m.py:1" in text


# --------------------------------------------------------------------------
# survivor pattern summary
# --------------------------------------------------------------------------

def test_summarize_empty_returns_empty_string():
    assert summarize_survivor_patterns([]) == ""


def test_summarize_reports_most_common():
    ops = [tx.COMPARISON_BOUNDARY] * 3 + [tx.ARITHMETIC_REPLACEMENT] * 2 + [tx.OTHER]
    summary = summarize_survivor_patterns(ops)
    assert "comparison boundary (3)" in summary
    assert "arithmetic replacement (2)" in summary


# --------------------------------------------------------------------------
# SHAP attribution
# --------------------------------------------------------------------------

def test_attribution_returns_empty_without_a_model():
    """Attribution is diagnostic; losing it must never fail a run."""
    assert explain_prediction(None, {"operator": tx.COMPARISON_BOUNDARY}) == []
    assert top_contributing_feature(None, {"operator": tx.COMPARISON_BOUNDARY}) == "unavailable"


@pytest.mark.skipif(load_model() is None, reason="no trained model present")
def test_attribution_names_a_real_feature():
    from features.extractor import extract_features

    model = load_model()
    module = SERVICE_ROOT / "src" / "main.py"
    features = extract_features(module, 100, tx.COMPARISON_BOUNDARY, True)
    name = top_contributing_feature(model, features)
    assert name in model.feature_names
