"""
Feature extraction, storage, and vectorisation tests.

All DB writes land in the session temp database (see conftest.py) -- these
tests used to write into the real training set.
"""
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from features.extractor import NUMERIC_FEATURES, extract_features  # noqa: E402
from ml.prioritizer import feature_names, features_to_vector  # noqa: E402
from mutators import taxonomy as tx  # noqa: E402
from storage import db  # noqa: E402


@pytest.fixture
def sample_module(tmp_path):
    """Two functions, so function-scoped features can be shown to differ."""
    p = tmp_path / "sample.py"
    p.write_text(
        "def simple(a):\n"                       # 1
        "    return a + 1\n"                     # 2
        "\n"                                     # 3
        "\n"                                     # 4
        "def complicated(a, b, c=3):\n"          # 5
        "    total = 0\n"                        # 6
        "    for x in range(a):\n"               # 7
        "        if x > b and x < c:\n"          # 8
        "            total += x\n"               # 9
        "        elif x == b:\n"                 # 10
        "            continue\n"                 # 11
        "    return total\n",                    # 12
        encoding="utf-8",
    )
    return p


def test_feature_keys_are_stable(sample_module):
    f = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, True)
    expected = set(NUMERIC_FEATURES) | {"operator", "operator_family", "line_number"}
    assert set(f.keys()) == expected


def test_features_are_function_scoped_not_file_scoped(sample_module):
    """The v1 defect: complexity and ast_depth were computed over the whole
    module, so every mutant in a file carried identical values. With 155 rows
    across 4 files there were exactly 4 distinct pairs -- the features WERE a
    module identifier, which a random split lets the model memorise."""
    simple = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, True)
    complicated = extract_features(sample_module, 9, tx.ARITHMETIC_REPLACEMENT, True)

    assert simple["func_complexity"] < complicated["func_complexity"]
    assert simple["func_loc"] < complicated["func_loc"]
    assert simple["func_num_params"] == 1
    assert complicated["func_num_params"] == 3


def test_context_flags_reflect_real_position(sample_module):
    inner = extract_features(sample_module, 9, tx.ARITHMETIC_REPLACEMENT, True)
    assert inner["in_loop"] == 1
    assert inner["in_conditional"] == 1

    outer = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, True)
    assert outer["in_loop"] == 0
    assert outer["in_conditional"] == 0


def test_rel_position_within_function(sample_module):
    first = extract_features(sample_module, 6, tx.ARITHMETIC_REPLACEMENT, True)
    last = extract_features(sample_module, 12, tx.ARITHMETIC_REPLACEMENT, True)
    assert 0.0 <= first["rel_position_in_func"] < last["rel_position_in_func"] <= 1.0


def test_unresolved_line_degrades_gracefully(sample_module):
    """A mutant whose line could not be resolved must still produce a usable
    feature dict rather than raising -- it is recorded, not dropped."""
    f = extract_features(sample_module, None, tx.OTHER, False)
    assert f["line_number"] == -1
    assert f["func_complexity"] == 0


def test_vector_length_matches_feature_names(sample_module):
    f = extract_features(sample_module, 9, tx.COMPARISON_BOUNDARY, True)
    vector = features_to_vector(f)
    assert len(vector) == len(feature_names())
    assert len(vector) == len(tx.FAMILIES) + len(NUMERIC_FEATURES)


def test_vector_one_hot_encodes_the_family(sample_module):
    f = extract_features(sample_module, 8, tx.COMPARISON_BOUNDARY, True)
    vector = features_to_vector(f)
    one_hot = vector[:len(tx.FAMILIES)]
    assert sum(one_hot) == 1.0
    assert one_hot[tx.FAMILIES.index(tx.FAMILY_COMPARISON)] == 1.0


def test_storage_roundtrip_is_isolated_from_the_real_dataset(sample_module):
    f = extract_features(sample_module, 9, tx.ARITHMETIC_REPLACEMENT, True)
    db.insert_record(
        "test-run", "corpus/sample/sample.py", "sample.x_complicated__mutmut_1",
        f, "active_survivor", module_slug="sample", suite="weak",
        function_name="complicated", triage_status="active",
        triage_confidence=1.0, triage_reason_code="no_arid_pattern_matched",
    )
    rows = db.fetch_all_records()
    assert any(r["mutant_id"] == "sample.x_complicated__mutmut_1" for r in rows)

    row = next(r for r in rows if r["mutant_id"] == "sample.x_complicated__mutmut_1")
    assert row["module_key"] == "corpus/sample/sample.py"
    assert row["dataset_schema_version"] == db.DATASET_SCHEMA_VERSION
    # triage provenance is persisted, not discarded as it was in v1
    assert row["triage_confidence"] == 1.0
    assert row["triage_reason_code"] == "no_arid_pattern_matched"


def test_reinsert_updates_instead_of_duplicating(sample_module):
    """v1 appended unconditionally: 155 stored rows were 58 distinct mutants,
    one of them present 8 times, which put identical rows on both sides of the
    train/test split."""
    f = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, True)
    args = ("run-1", "corpus/dup/dup.py", "dup.x_simple__mutmut_1", f)
    db.insert_record(*args, "active_survivor", suite="weak")
    db.insert_record(*args, "killed", suite="weak")

    rows = [r for r in db.fetch_all_records()
            if r["mutant_id"] == "dup.x_simple__mutmut_1" and r["module_key"] == "corpus/dup/dup.py"]
    assert len(rows) == 1, f"expected 1 row after re-insert, got {len(rows)}"
    assert rows[0]["outcome"] == "killed"


def test_changed_lines_are_persisted_for_offline_replay(sample_module):
    """Storing the changed line is what makes triage rules replayable without
    re-running mutmut. Before this, iterating on a filter rule cost a 30-60 min
    Linux-only collection run just to observe the effect -- which is why the
    equivalence-filter defect took a full round trip to diagnose."""
    f = extract_features(sample_module, 2, tx.STRING_LITERAL_CHANGE, True)
    db.insert_record(
        "replay", "corpus/sample/sample.py", "sample.x_simple__mutmut_9", f,
        "active_survivor", suite="weak",
        removed_line='    return "A"', added_line='    return "XXAXX"',
    )
    row = next(r for r in db.fetch_all_records()
               if r["mutant_id"] == "sample.x_simple__mutmut_9")
    assert row["removed_line"] == '    return "A"'
    assert row["added_line"] == '    return "XXAXX"'


def test_changed_lines_are_optional(sample_module):
    """Rows collected before this column existed must still insert."""
    f = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, True)
    db.insert_record("nolines", "corpus/sample/sample.py", "sample.x_simple__mutmut_10",
                     f, "killed", suite="weak")
    row = next(r for r in db.fetch_all_records()
               if r["mutant_id"] == "sample.x_simple__mutmut_10")
    assert row["removed_line"] is None


def test_training_population_excludes_uncovered_and_arid(sample_module):
    """Training must match inference conditions. `covered = 0` implies the line
    never runs, which implies the mutant cannot be killed -- so including those
    rows lets the model learn a tautology it never faces at serving time."""
    covered = extract_features(sample_module, 9, tx.ARITHMETIC_REPLACEMENT, True)
    uncovered = extract_features(sample_module, 2, tx.ARITHMETIC_REPLACEMENT, False)

    db.insert_record("pop", "corpus/pop/pop.py", "pop.x_a__mutmut_1", covered,
                     "active_survivor", suite="weak", triage_status="active")
    db.insert_record("pop", "corpus/pop/pop.py", "pop.x_b__mutmut_1", uncovered,
                     "uncovered", suite="weak", triage_status="active")
    db.insert_record("pop", "corpus/pop/pop.py", "pop.x_c__mutmut_1", covered,
                     "equivalent_suspected", suite="weak",
                     triage_status="equivalent_suspected")
    db.insert_record("pop", "corpus/pop/pop.py", "pop.x_d__mutmut_1", covered,
                     "killed", suite="strong", triage_status="active")

    ids = {r["mutant_id"] for r in db.fetch_training_population()}
    assert "pop.x_a__mutmut_1" in ids
    assert "pop.x_b__mutmut_1" not in ids   # uncovered
    assert "pop.x_c__mutmut_1" not in ids   # suspected equivalent
    assert "pop.x_d__mutmut_1" not in ids   # oracle suite, not the shipped one
