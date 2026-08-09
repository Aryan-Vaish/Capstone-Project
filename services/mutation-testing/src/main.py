"""
Intelligent Mutation Testing Service -- FastAPI entrypoint
==============================================================
Day 3 status: every mutant from every run is now persisted (storage/db.py)
with extracted features (features/extractor.py), building the training
dataset. If a trained model exists (ml/model.json, produced by running
ml/train.py separately), active survivors are scored and sorted by
ml_priority_score. If no model exists yet, ml_priority_score stays None
-- same graceful-degradation pattern as Day 1/2's TODOs.

Explainability (missing_edge_case, suggested_test_stub) is still TODO -- Day 4.
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
from features.extractor import extract_features
from storage.db import insert_record
from ml.prioritizer import load_model, predict_priority
from shared.schemas.mutation_schema import MutationEvidenceObject, MutationRunRequest, SurvivingMutant

app = FastAPI(title="Intelligent Mutation Testing Service", version="1.0")

_ML_MODEL = load_model()  # None if ml/model.json doesn't exist yet


@app.get("/health")
def health():
    return {"status": "ok", "service": "mutation-testing", "ml_model_loaded": _ML_MODEL is not None}


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
    active_survivors = []       # list of (Mutant, features_dict)

    for m in mutants:
        covered = m.line_number is not None and m.line_number in covered_lines
        features = extract_features(source_path, m.line_number, m.operator, covered)

        if m.status != "survived":
            insert_record(run_id, source_path.name, m.mutant_id, features, "killed")
            continue

        if not covered:
            uncovered += 1
            insert_record(run_id, source_path.name, m.mutant_id, features, "uncovered")
            continue

        triage = triage_mutant(m.diff)
        if triage.status == "equivalent_suspected":
            equivalent_filtered += 1
            insert_record(run_id, source_path.name, m.mutant_id, features, "equivalent_suspected")
            continue

        insert_record(run_id, source_path.name, m.mutant_id, features, "active_survivor")
        active_survivors.append((m, features))

    survived = len(active_survivors) + uncovered + equivalent_filtered
    mutation_score = round(killed / total, 3) if total else 0.0

    # Score + sort active survivors if a trained model is available
    scored = []
    for m, features in active_survivors:
        score = predict_priority(_ML_MODEL, features) if _ML_MODEL else None
        scored.append((m, score))
    scored.sort(key=lambda pair: (pair[1] is None, -(pair[1] or 0)))

    survivor_objects = [
        SurvivingMutant(
            mutant_id=m.mutant_id,
            operator=m.operator,
            location=f"{req.source_file}:{m.line_number}",
            ml_priority_score=score,
            missing_edge_case=None,        # TODO: wire in explainability/ (Day 4)
            suggested_test_stub=None,
        )
        for m, score in scored
    ]

    model_note = "ML-prioritized" if _ML_MODEL else "unprioritized (no trained model yet -- run ml/train.py)"
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
            f"{killed} killed). {model_note}."
        ),
    )
