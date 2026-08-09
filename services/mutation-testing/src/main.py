"""
Intelligent Mutation Testing Service -- FastAPI entrypoint
==============================================================
Day 1 status: real mutmut generation + execution wired in. ML
prioritization, equivalent-mutant triage, and explainability are still
TODO (see the ml/, triage/, explainability/ packages) -- this endpoint
currently returns every survivor with ml_priority_score=None rather
than mock data, which is the correct intermediate state: real numbers,
un-prioritized.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# repo root (four levels up: src -> mutation-testing -> services -> repo root)
# needed so `shared.schemas.mutation_schema` resolves regardless of cwd.
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from harness.executor import run_mutation_analysis
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

    mutants = run_mutation_analysis(source_path, tests_dir_path)

    total = len(mutants)
    killed = sum(1 for m in mutants if m.status == "killed")
    survived = sum(1 for m in mutants if m.status == "survived")
    mutation_score = round(killed / total, 3) if total else 0.0

    survivors = [
        SurvivingMutant(
            mutant_id=m.mutant_id,
            operator=m.operator,
            location=f"{req.source_file}:{m.line_number}",
            ml_priority_score=None,       # TODO: wire in ml/ prioritizer (Day 3)
            missing_edge_case=None,        # TODO: wire in explainability/ (Day 4)
            suggested_test_stub=None,
        )
        for m in mutants if m.status == "survived"
    ]

    return MutationEvidenceObject(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        mutation_score=mutation_score,
        total_mutants=total,
        killed=killed,
        survived=survived,
        uncovered_mutants=0,               # TODO: wire in coverage/ (Day 2)
        equivalent_mutants_filtered=0,      # TODO: wire in triage/ (Day 2)
        prioritized_surviving_mutants=survivors,
        explanation_summary=f"{survived}/{total} mutants survived (raw, unfiltered, unprioritized).",
    )
