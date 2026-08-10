"""
End-to-end pipeline tests.

Tests that actually invoke mutmut are marked `requires_mutmut` and skip when it
is unavailable (it has no Windows support). Everything that can be asserted
without running a mutation engine deliberately is -- the schema shape, the score
arithmetic, and the fact that explanations do not depend on a trained model.
"""
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from main import app  # noqa: E402

client = TestClient(app)

CORPUS = SERVICE_ROOT / "corpus"
HAS_MUTMUT = shutil.which("mutmut") is not None
requires_mutmut = pytest.mark.skipif(
    not HAS_MUTMUT,
    reason="mutmut not installed (no native Windows support -- run under WSL)",
)


def _first_corpus_module():
    """Works with both the pre- and post-migration corpus layouts."""
    legacy = CORPUS / "example" / "module.py"
    if legacy.exists():
        return legacy, CORPUS / "example"
    for meta in sorted(CORPUS.glob("*/meta.json")):
        d = meta.parent
        src = d / f"{d.name.split('_', 1)[-1]}.py"
        if src.exists() and (d / "tests_weak").exists():
            return src, d / "tests_weak"
    return None, None


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # /health must surface whether the loaded model is stale relative to the
    # code's taxonomy -- an absent or mismatched model should be visible here
    # rather than discovered via silently-None priority scores.
    assert "ml_model_loaded" in body
    assert "code_taxonomy_version" in body
    assert "model_stale_reason" in body


def test_missing_source_file_is_404():
    resp = client.post("/mutation/run", json={
        "source_file": "corpus/does_not_exist/module.py",
        "tests_dir": "corpus",
    })
    assert resp.status_code == 404


@requires_mutmut
def test_mutation_run_real_execution():
    src, tests = _first_corpus_module()
    assert src is not None, "no usable corpus module found"
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["module"] == "mutation_testing"
    assert body["total_mutants"] > 0
    assert body["killed"] + body["survived"] == body["total_mutants"]
    assert 0.0 <= body["mutation_score"] <= 1.0


@requires_mutmut
def test_survivor_buckets_partition_exactly():
    """Every survivor lands in exactly one bucket: active, uncovered, or
    suspected-equivalent. Nothing is dropped, nothing double-counted."""
    src, tests = _first_corpus_module()
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    body = resp.json()
    accounted = (len(body["prioritized_surviving_mutants"])
                 + body["uncovered_mutants"]
                 + body["equivalent_mutants_filtered"])
    assert accounted == body["survived"]
    assert body["active_survivors"] == len(body["prioritized_surviving_mutants"])


@requires_mutmut
def test_line_numbers_are_absolute_file_coordinates():
    """The dataset-v1 blocker, asserted end-to-end.

    Every reported location must point at a line that actually exists in the
    source file. v1 emitted function-relative offsets, so a mutant in the second
    function of a file pointed at line 1 or 2 of the file instead.
    """
    src, tests = _first_corpus_module()
    n_lines = len(src.read_text(encoding="utf-8").splitlines())
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    body = resp.json()
    for survivor in body["prioritized_surviving_mutants"]:
        if ":" not in survivor["location"]:
            continue
        line = int(survivor["location"].rsplit(":", 1)[1])
        assert 1 <= line <= n_lines, (
            f"{survivor['mutant_id']} reports line {line}, file has {n_lines}"
        )


@requires_mutmut
def test_unclassified_operator_rate_is_low():
    """Taxonomy v1 left 73.5% of mutants as `unknown`. v2 targets under 5%."""
    src, tests = _first_corpus_module()
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    body = resp.json()
    total = body["total_mutants"]
    rate = body["unclassified_mutants"] / total if total else 0.0
    assert rate < 0.05, f"unclassified operator rate {rate:.1%} (was 73.5% in v1)"


@requires_mutmut
def test_explanations_present_without_a_trained_model():
    """The v1 coupling bug: templated NLG was gated behind `if _ML_MODEL`, so a
    fresh checkout (where model.json is an absent build artifact) returned
    missing_edge_case=None for every mutant. Explanations are deterministic and
    must never depend on the model; only the score may degrade to None."""
    src, tests = _first_corpus_module()
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    body = resp.json()
    if not body["prioritized_surviving_mutants"]:
        pytest.skip("no active survivors on this module")
    for survivor in body["prioritized_surviving_mutants"]:
        assert survivor["missing_edge_case"], "explanation missing"
        assert survivor["suggested_test_stub"], "stub missing"
        assert survivor["ml_priority_score"] is None or isinstance(
            survivor["ml_priority_score"], float
        )


@requires_mutmut
def test_three_scores_are_consistent():
    src, tests = _first_corpus_module()
    resp = client.post("/mutation/run", json={
        "source_file": str(src), "tests_dir": str(tests),
    })
    b = resp.json()
    for key in ("mutation_score", "mutation_score_adjusted", "test_strength"):
        assert 0.0 <= b[key] <= 1.0
    # adjusted removes suspected equivalents from the denominator, so it can
    # only be >= the raw coverage-style score
    assert b["mutation_score_adjusted"] >= b["mutation_score"] - 1e-9
    assert set(b["score_definitions"]) == {
        "mutation_score", "mutation_score_adjusted", "test_strength"
    }
