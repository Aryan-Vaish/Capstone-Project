"""
Tests for the collection diagnostic.

Builds a synthetic dataset with KNOWN properties -- planted triage false
positives, a planted integrity contradiction, a planted asymmetric module -- and
checks the diagnostic reports each one. Shipping this script to a collaborator
untested would defeat its purpose: it exists to be trusted about numbers nobody
else can see.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from storage.db import DATASET_SCHEMA_VERSION, connection, insert_record  # noqa: E402

SCRIPT = SERVICE_ROOT / "scripts" / "analyze_collection.py"


def _features(operator="string_literal_change", family="constant", covered=1, line=3):
    return {
        "operator": operator, "operator_family": family, "line_number": line,
        "func_complexity": 2, "func_loc": 5, "func_num_params": 1,
        "func_num_returns": 1, "func_num_branches": 1, "node_depth_at_line": 3,
        "rel_position_in_func": 0.5, "in_conditional": 0, "in_loop": 0,
        "in_exception": 0, "covered": covered, "n_covering_tests": 1,
    }


def _add(conn, module, suite, mid, outcome, *, triage="active",
         reason="no_arid_pattern_matched", conf=1.0, covered=1,
         operator="string_literal_change", removed='    return "A"'):
    insert_record(
        "run-1", f"corpus/{module}/{module.split('_', 1)[1]}.py", mid,
        _features(operator=operator, covered=covered), outcome,
        module_slug=module, suite=suite, function_name="f",
        removed_line=removed, added_line=removed.replace('"A"', '"XXAXX"'),
        triage_status=triage, triage_confidence=conf, triage_reason_code=reason,
        taxonomy_version="v2", conn=conn,
    )


@pytest.fixture
def synthetic_db(tmp_path, monkeypatch):
    """A dataset whose every interesting property is known in advance."""
    db = tmp_path / "synthetic.db"
    monkeypatch.setenv("MUTATION_DB_PATH", str(db))

    with connection() as conn:
        # --- 01: strong tier. 8 killed / 2 survived. Two flagged-but-KILLED,
        # i.e. planted triage false positives.
        # Two mutants are flagged arid by the weak suite. The collector
        # overwrites `outcome` for flagged rows, so outcome == low_value, NOT
        # killed -- the combination the old fixture used cannot occur in real
        # data. The strong suite kills both, refuting the flag. That refutation
        # is the only real measurement of filter error (section 4).
        for i in range(8):
            flagged = i < 2
            _add(conn, "01_withdraw_ledger", "weak", f"m.x_f__mutmut_{i}",
                 "low_value" if flagged else "killed",
                 triage="low_value" if flagged else "active",
                 reason="logging_call" if flagged else "no_arid_pattern_matched",
                 conf=0.7 if flagged else 1.0)
        for i in range(8, 10):
            _add(conn, "01_withdraw_ledger", "weak", f"m.x_f__mutmut_{i}", "active_survivor")
        for i in range(10):
            _add(conn, "01_withdraw_ledger", "strong", f"m.x_f__mutmut_{i}", "killed")

        # --- 03: weak tier. 2 killed / 8 survived. Strong kills 6 of the
        # survivors -> 6 true gaps.
        for i in range(2):
            _add(conn, "03_username_validator", "weak", f"u.x_g__mutmut_{i}", "killed")
        for i in range(2, 10):
            _add(conn, "03_username_validator", "weak", f"u.x_g__mutmut_{i}",
                 "active_survivor")
        for i in range(8):
            _add(conn, "03_username_validator", "strong", f"u.x_g__mutmut_{i}", "killed")
        for i in range(8, 10):
            _add(conn, "03_username_validator", "strong", f"u.x_g__mutmut_{i}",
                 "active_survivor")

        # --- 02: medium tier, sits between the other two.
        for i in range(5):
            _add(conn, "02_discount_pricing", "weak", f"d.x_h__mutmut_{i}", "killed")
        for i in range(5, 10):
            _add(conn, "02_discount_pricing", "weak", f"d.x_h__mutmut_{i}",
                 "active_survivor")
        # deliberately ASYMMETRIC: only 9 strong rows for 10 weak mutants
        for i in range(9):
            _add(conn, "02_discount_pricing", "strong", f"d.x_h__mutmut_{i}", "killed")

        # --- 08: untested_function. Half the mutants never execute.
        for i in range(5):
            _add(conn, "08_rate_limiter", "weak", f"r.x_k__mutmut_{i}", "killed")
        for i in range(5, 10):
            _add(conn, "08_rate_limiter", "weak", f"r.x_k__mutmut_{i}", "uncovered",
                 covered=0)
        for i in range(10):
            _add(conn, "08_rate_limiter", "strong", f"r.x_k__mutmut_{i}", "killed")

    return db


def run_script(db: Path, *extra) -> str:
    env = dict(os.environ, MUTATION_DB_PATH=str(db))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True, text=True, env=env, cwd=str(SERVICE_ROOT),
    )
    assert result.returncode == 0, f"script failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def test_script_runs_end_to_end(synthetic_db):
    out = run_script(synthetic_db)
    for heading in ("INTEGRITY", "PER-MODULE LEDGER", "EQUIVALENCE-FILTER PRECISION",
                    "ORACLE ADEQUACY", "ORACLE GAP LABELS", "TIER VALIDATION", "SUMMARY"):
        assert heading in out, f"missing section: {heading}"


def test_detects_planted_triage_false_positives(synthetic_db):
    """Two mutants flagged arid by the weak suite are KILLED by the strong
    suite. Section 4 must refute both, and Gate 4 must report 0.000 and fail."""
    out = run_script(synthetic_db)
    assert "GATE 4" in out
    assert "0.000" in out, f"expected refuted precision 0.000:\n{out}"
    assert "BELOW the 0.85 gate" in out


def test_section_2_precision_is_tautological(synthetic_db):
    """Section 2 asks whether a flagged mutant was killed by the SAME suite.
    The collector overwrites `outcome` for flagged rows, so that query can
    never return a kill: section 2 reports 1.000 even with planted false
    positives that section 4 correctly refutes. This test pins the defect so
    nobody trusts section 2 again. See ADR-005."""
    out = run_script(synthetic_db)
    assert "cannot fail by construction" in out


def test_detects_asymmetric_module(synthetic_db):
    """02 has 10 weak mutants but only 9 strong rows -- joins are unreliable."""
    out = run_script(synthetic_db)
    assert "ASYMMETRIC" in out
    assert "02_discount_pricing" in out


def test_counts_true_gaps_excluding_coverage_gaps(synthetic_db):
    """03 has 8 weak survivors, 6 of which the oracle kills => 6 true gaps.
    08's 5 uncovered-then-killed mutants are coverage gaps and must NOT be
    pooled into the positive class."""
    out = run_script(synthetic_db)
    assert "TRUE GAP = survives the weak suite" in out
    data = json.loads(_json_report(synthetic_db))
    gaps = data["gap_labels"]
    assert gaps["per_module"]["03_username_validator"]["true_gap_covered"] == 6
    assert gaps["per_module"]["08_rate_limiter"].get("true_gap_covered", 0) == 0
    assert gaps["per_module"]["08_rate_limiter"]["true_gap_coverage"] == 5


def test_reports_three_valued_tier_verdict(synthetic_db):
    """BELOW_FLOOR vs ABOVE_CEILING is what makes the diagnosis falsifiable."""
    data = json.loads(_json_report(synthetic_db))
    verdicts = set(data["tiers"]["verdicts"].values())
    assert verdicts <= {"OK", "BELOW_FLOOR", "ABOVE_CEILING"}


def test_trend_test_runs_under_both_metrics(synthetic_db):
    data = json.loads(_json_report(synthetic_db))
    for key in ("trend_mutation_score", "trend_mutation_score_adjusted"):
        assert key in data["tiers"]
        assert data["tiers"][key]["p_value"] is not None


def test_untested_function_excluded_from_the_ordinal(synthetic_db):
    """08 is a coverage claim, not a point on the strength scale."""
    data = json.loads(_json_report(synthetic_db))
    excluded = data["tiers"]["trend_mutation_score"]["excluded"]
    assert any(e["module"] == "08_rate_limiter" for e in excluded)


def test_json_report_is_the_only_write(synthetic_db, tmp_path):
    before = set((SERVICE_ROOT / "reports").glob("*")) if (SERVICE_ROOT / "reports").exists() else set()
    run_script(synthetic_db)          # no --json-out
    after = set((SERVICE_ROOT / "reports").glob("*")) if (SERVICE_ROOT / "reports").exists() else set()
    assert before == after, "default invocation must not write anything"


def _json_report(db: Path) -> str:
    """Run with --json-out and return the report body."""
    run_script(db, "--json-out")
    reports = sorted((SERVICE_ROOT / "reports").glob("analysis_*.json"))
    assert reports, "no analysis json produced"
    return reports[-1].read_text(encoding="utf-8")


def test_empty_database_exits_cleanly(tmp_path, monkeypatch):
    db = tmp_path / "empty.db"
    monkeypatch.setenv("MUTATION_DB_PATH", str(db))
    with connection():
        pass
    env = dict(os.environ, MUTATION_DB_PATH=str(db))
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                            text=True, env=env, cwd=str(SERVICE_ROOT))
    assert result.returncode == 1
    assert "Run scripts/collect_dataset.py first" in result.stdout


def test_missing_database_fails_with_the_resolved_path(tmp_path, monkeypatch):
    missing = tmp_path / "nope.db"
    env = dict(os.environ, MUTATION_DB_PATH=str(missing))
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                            text=True, env=env, cwd=str(SERVICE_ROOT))
    assert result.returncode != 0
    assert str(missing) in (result.stdout + result.stderr)
