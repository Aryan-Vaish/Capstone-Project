"""
Intelligent Mutation Testing Service -- FastAPI entrypoint
==============================================================
Day 2 status: coverage gating + Tier 0 equivalent-mutant triage wired in,
on top of Day 1's real mutmut execution.

Every surviving mutant is now bucketed into exactly one of three groups:
  1. uncovered            -- no stable test touches this line at all
                              (a coverage gap, NOT a test-quality gap)
  2. equivalent_suspected -- covered, but Tier 0 static rules flag it as
                              likely-equivalent (logging/docstring/repr/
                              string-literal-only changes) -- NEVER deleted,
                              just tagged, since Governance needs the count
  3. active               -- a genuine, covered, non-equivalent survivor --
                              these are the only ones that go into
                              prioritized_surviving_mutants

ML prioritization and explainability (missing_edge_case, suggested_test_stub,
ml_priority_score) are still TODO -- Day 3/4.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from harness.executor import run_mutation_analysis
from triage.arid_filter import triage_mutant
from shared.schemas.mutation_schema import MutationEvidenceObject, MutationRunRequest, SurvivingMutant

app = FastAPI(title="Intelligent Mutation Testing Service", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok", "service": "mutation-testing"}


@app.post("/mutation/run", response_model=MutationEvidenceObject)
def run_mutation(req: MutationRunRequest):
    run_id = req.run_id or str(uuid4())
    source_path = Path(req.source_file)
    tests_dir_path = Path(req.tests_dir)

    if not source_path.exists():
        raise HTTPException(404, f"source_file not found: {source_path}")
    if not tests_dir_path.exists():
        raise HTTPException(404, f"tests_dir not found: {tests_dir_path}")

    mutants, covered_lines = run_mutation_analysis(source_path, tests_dir_path)

    total = len(mutants)
    killed = sum(1 for m in mutants if m.status == "killed")

    uncovered = 0
    equivalent_filtered = 0
    active_survivors = []

    for m in mutants:
        if m.status != "survived":
            continue
        if m.line_number is not None and m.line_number not in covered_lines:
            uncovered += 1
            continue
        triage = triage_mutant(m.diff)
        if triage.status == "equivalent_suspected":
            equivalent_filtered += 1
            continue
        active_survivors.append(m)

    survived = len(active_survivors) + uncovered + equivalent_filtered
    mutation_score = round(killed / total, 3) if total else 0.0

    survivor_objects = [
        SurvivingMutant(
            mutant_id=m.mutant_id,
            operator=m.operator,
            location=f"{req.source_file}:{m.line_number}",
            ml_priority_score=None,       # TODO: wire in ml/ prioritizer (Day 3)
            missing_edge_case=None,        # TODO: wire in explainability/ (Day 4)
            suggested_test_stub=None,
        )
        for m in active_survivors
    ]

    return MutationEvidenceObject(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mutation_score=mutation_score,
        total_mutants=total,
        killed=killed,
        survived=survived,
        uncovered_mutants=uncovered,
        equivalent_mutants_filtered=equivalent_filtered,
        prioritized_surviving_mutants=survivor_objects,
        explanation_summary=(
            f"{len(active_survivors)} active survivor(s) out of {total} total mutants "
            f"({uncovered} uncovered, {equivalent_filtered} suspected-equivalent, "
            f"{killed} killed). Raw/unprioritized -- ML ranking not yet wired in."
        ),
    )
