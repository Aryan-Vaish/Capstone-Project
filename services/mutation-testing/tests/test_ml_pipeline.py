"""
Day 3 tests: storage + feature extraction. These are deliberately
standalone (don't require a trained model to exist) so they pass on a
fresh clone before anyone has run ml/train.py yet.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storage.db import init_db, insert_record, fetch_all_records
from features.extractor import extract_features


def test_feature_extraction_shape():
    corpus_module = Path(__file__).resolve().parents[1] / "corpus" / "example" / "module.py"
    features = extract_features(corpus_module, 2, "conditional_boundary", True)
    expected_keys = {"operator", "line_number", "complexity", "ast_depth",
                      "in_conditional", "in_loop", "in_exception", "covered"}
    assert set(features.keys()) == expected_keys
    assert features["in_conditional"] == 1   # line 2 is inside the `if` in withdraw()


def test_storage_insert_and_fetch():
    init_db()
    before = len(fetch_all_records())
    features = {"operator": "unknown", "line_number": 1, "complexity": 1,
                "ast_depth": 1, "in_conditional": 0, "in_loop": 0,
                "in_exception": 0, "covered": 1}
    insert_record("test-run-day3", "test_module.py", "test.mutant.1", features, "killed")
    after = len(fetch_all_records())
    assert after == before + 1
