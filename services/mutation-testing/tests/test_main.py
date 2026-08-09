import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_mutation_run_real_execution():
    """Runs real mutmut against the corpus example -- not mocked.
    Requires corpus/example/module.py + test_module.py to exist."""
    resp = client.post("/mutation/run", json={
        "source_file": "corpus/example/module.py",
        "tests_dir": "corpus/example",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["module"] == "mutation_testing"
    assert body["total_mutants"] > 0
    assert body["killed"] + body["survived"] == body["total_mutants"]
    assert 0.0 <= body["mutation_score"] <= 1.0


def test_coverage_and_triage_bucketing():
    """Day 2: confirms coverage gating + Tier 0 triage actually partition
    survivors correctly, not just that the endpoint returns 200."""
    resp = client.post("/mutation/run", json={
        "source_file": "corpus/example/module.py",
        "tests_dir": "corpus/example",
    })
    body = resp.json()
    # every survived mutant must be accounted for in exactly one bucket
    accounted = (
        len(body["prioritized_surviving_mutants"])
        + body["uncovered_mutants"]
        + body["equivalent_mutants_filtered"]
    )
    assert accounted == body["survived"]
    # on this toy module, we know 3 mutants are string-literal-only
    # message changes -- they should be filtered, not reported as real gaps
    assert body["equivalent_mutants_filtered"] >= 1
