"""
Day 4 tests: templated NLG (deterministic, no model needed) + full
pipeline integration (requires a trained model -- skips gracefully if
ml/model.json doesn't exist yet, matching the graceful-degradation
pattern used throughout main.py).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from explainability.test_gap import generate_missing_edge_case, generate_test_stub, summarize_survivor_patterns


def test_generate_missing_edge_case_known_operator():
    text = generate_missing_edge_case("conditional_boundary", "module.py:5")
    assert "module.py:5" in text
    assert "boundary" in text.lower()


def test_generate_missing_edge_case_unknown_operator_has_fallback():
    text = generate_missing_edge_case("some_new_operator_not_in_templates", "module.py:5")
    assert "module.py:5" in text
    assert text  # never empty, always falls back to the "unknown" template


def test_generate_test_stub_is_valid_looking_python():
    stub = generate_test_stub("negate_conditional", "module.py:10", "my_function")
    assert stub.startswith("def test_")
    assert "..." in stub


def test_summarize_survivor_patterns_empty():
    summary = summarize_survivor_patterns([])
    assert "No active survivors" in summary


def test_summarize_survivor_patterns_with_data():
    summary = summarize_survivor_patterns(["conditional_boundary", "conditional_boundary", "unknown"])
    assert "conditional boundary" in summary


def test_full_pipeline_explanations_present_when_model_exists():
    """Integration test: if a trained model exists, real explanations must
    be populated (not None) for every active survivor."""
    from ml.prioritizer import load_model
    if load_model() is None:
        import pytest
        pytest.skip("No trained model present -- run ml/train.py first to exercise this test")

    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    resp = client.post("/mutation/run", json={
        "source_file": "corpus/example/module.py",
        "tests_dir": "corpus/example",
    })
    body = resp.json()
    for survivor in body["prioritized_surviving_mutants"]:
        assert survivor["missing_edge_case"] is not None
        assert survivor["suggested_test_stub"] is not None
        assert survivor["ml_priority_score"] is not None
