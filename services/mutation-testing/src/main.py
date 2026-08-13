"""
Intelligent Mutation Testing Service -- FastAPI entrypoint
=============================================================
Pipeline: source + tests -> real mutants (mutmut) -> coverage gating ->
equivalent-mutant triage -> feature extraction -> ML prioritisation ->
human-readable missing-test explanations.

Requires WSL/Linux: mutmut has no native Windows support.

FIXES IN THIS REVISION (see docs/Architecture/ ADRs for the full rationale)
--------------------------------------------------------------------------
* Mutant line numbers are now absolute file coordinates. They used to be
  offsets within the mutated function, so the coverage gate below was comparing
  function-relative numbers against file-absolute coverage data -- wrong for
  every function that was not first in its file.
* Explanations are no longer gated on a trained model. Templated NLG is
  deterministic; gating it meant a fresh checkout (where model.json, a build
  artifact, is absent) returned `missing_edge_case: None` for every mutant.
* Module identity is a real path key, not `source_path.name` -- which was
  "module.py" for every corpus module and made module-aware evaluation
  impossible.
* Triage confidence and reason code are persisted rather than discarded.
* Three explicitly-defined scores are published instead of one ambiguous one.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException

from explainability.attribution import top_contributing_feature
from explainability.test_gap import (
    generate_missing_edge_case,
    generate_test_stub,
    summarize_survivor_patterns,
)
from features.extractor import extract_features
from harness.executor import run_mutation_analysis
from ml.prioritizer import load_model, predict_priority
from mutators.taxonomy import OTHER, TAXONOMY_VERSION
from shared.schemas.mutation_schema import (
    MutationEvidenceObject,
    MutationRunRequest,
    SurvivingMutant,
)
from storage.db import DATASET_SCHEMA_VERSION, connection, insert_record
from triage.arid_filter import triage_mutant

app = FastAPI(title="Intelligent Mutation Testing Service", version="1.1")

_ML_MODEL = load_model()          # None until ml/train.py has been run
REPORTS_DIR = SERVICE_ROOT / "reports"


def _git_sha(path: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _module_key(source_path: Path, override: Optional[str]) -> str:
    """Stable identity for a source module, used for grouping and splitting.

    v1 used `source_path.name`, so all three corpus modules -- each named
    module.py -- collapsed to a single identity. That is what made module-aware
    cross-validation and held-out benchmarking impossible.
    """
    if override:
        return override
    try:
        return source_path.resolve().relative_to(SERVICE_ROOT).as_posix()
    except ValueError:
        return source_path.resolve().as_posix()


@app.get("/health")
def health():
    stale = _ML_MODEL.check_compatible() if _ML_MODEL else None
    return {
        "status": "ok",
        "service": "mutation-testing",
        "ml_model_loaded": _ML_MODEL is not None,
        "model_taxonomy_version": _ML_MODEL.taxonomy_version if _ML_MODEL else None,
        "code_taxonomy_version": TAXONOMY_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "model_stale_reason": stale,
    }


@app.post("/mutation/run", response_model=MutationEvidenceObject)
def run_mutation(req: MutationRunRequest):
    run_id = req.run_id or str(uuid4())
    source_path = Path(req.source_file)
    tests_dir_path = Path(req.tests_dir)

    if not source_path.exists():
        raise HTTPException(404, f"source_file not found: {source_path}")
    if not tests_dir_path.exists():
        raise HTTPException(404, f"tests_dir not found: {tests_dir_path}")

    mutants, covered_lines, covering_counts = run_mutation_analysis(source_path, tests_dir_path)

    module_key = _module_key(source_path, req.module_id)
    module_slug = source_path.resolve().parent.name
    pipeline_sha = _git_sha(REPO_ROOT)

    total = len(mutants)
    killed = sum(1 for m in mutants if m.status == "killed")

    uncovered = 0
    equivalent_filtered = 0
    unresolved_lines = 0
    unclassified = 0
    active_survivors: List[tuple] = []
    filtered_audit: List[Dict] = []

    # One connection for the whole run rather than one per mutant.
    with connection() as conn:
        for m in mutants:
            if m.line_number is None:
                unresolved_lines += 1
            if m.operator == OTHER:
                unclassified += 1

            covered = m.line_number is not None and m.line_number in covered_lines
            features = extract_features(
                source_path, m.line_number, m.operator, covered,
                n_covering_tests=covering_counts.get(m.line_number, 0),
            )

            triage = triage_mutant(m.diff, function_name=m.qualified_name or m.function_name,
                                   operator=m.operator)
            common = dict(
                module_slug=module_slug,
                suite=req.suite,
                function_name=m.function_name,
                class_name=m.class_name,
                line_resolution=m.resolution_method,
                removed_line=m.removed_line,
                added_line=m.added_line,
                triage_status=triage.status,
                triage_confidence=triage.confidence,
                triage_reason_code=triage.reason_code,
                taxonomy_version=TAXONOMY_VERSION,
                pipeline_git_sha=pipeline_sha,
                conn=conn,
            )

            if m.status != "survived":
                insert_record(run_id, module_key, m.mutant_id, features, "killed", **common)
                continue

            if not covered:
                uncovered += 1
                insert_record(run_id, module_key, m.mutant_id, features, "uncovered", **common)
                continue

            if triage.status == "equivalent_suspected":
                equivalent_filtered += 1
                insert_record(run_id, module_key, m.mutant_id, features,
                              "equivalent_suspected", **common)
                filtered_audit.append({
                    "mutant_id": m.mutant_id,
                    "operator": m.operator,
                    "line": m.line_number,
                    "confidence": triage.confidence,
                    "reason_code": triage.reason_code,
                })
                continue

            insert_record(run_id, module_key, m.mutant_id, features, "active_survivor", **common)
            active_survivors.append((m, features))

    n_active = len(active_survivors)
    survived = n_active + uncovered + equivalent_filtered

    # Three explicitly-defined scores; see shared/schemas/mutation_schema.py.
    mutation_score = round(killed / total, 3) if total else 0.0
    adjusted_denom = total - equivalent_filtered
    mutation_score_adjusted = round(killed / adjusted_denom, 3) if adjusted_denom > 0 else 0.0
    strength_denom = killed + n_active
    test_strength = round(killed / strength_denom, 3) if strength_denom > 0 else 0.0

    scored = []
    for m, features in active_survivors:
        score = predict_priority(_ML_MODEL, features)
        scored.append((m, features, score))
    scored.sort(key=lambda triple: (triple[2] is None, -(triple[2] or 0)))

    survivor_objects = []
    attribution_audit = []
    for m, features, score in scored:
        location = f"{req.source_file}:{m.line_number}" if m.line_number else req.source_file
        stub_name = m.qualified_name or m.function_name or source_path.stem
        survivor_objects.append(SurvivingMutant(
            mutant_id=m.mutant_id,
            operator=m.operator,
            operator_family=features.get("operator_family", ""),
            location=location,
            function_name=m.qualified_name or m.function_name,
            ml_priority_score=score,
            # Deterministic templates -- deliberately NOT gated on the model.
            missing_edge_case=generate_missing_edge_case(m.operator, location),
            suggested_test_stub=generate_test_stub(m.operator, location, stub_name),
            line_resolution=m.resolution_method,
        ))
        if _ML_MODEL is not None:
            attribution_audit.append({
                "mutant_id": m.mutant_id,
                "ml_priority_score": score,
                "top_feature": top_contributing_feature(_ML_MODEL, features),
            })

    model_note = ("ML-prioritized" if _ML_MODEL
                  else "unprioritized (no trained model yet -- run ml/train.py)")
    pattern_summary = summarize_survivor_patterns([m.operator for m, _, _ in scored])

    _write_run_report(run_id, module_key, req.suite, filtered_audit,
                      attribution_audit, unresolved_lines, unclassified, total)

    return MutationEvidenceObject(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_mutants=total,
        killed=killed,
        survived=survived,
        uncovered_mutants=uncovered,
        equivalent_mutants_filtered=equivalent_filtered,
        active_survivors=n_active,
        unresolved_line_mutants=unresolved_lines,
        unclassified_mutants=unclassified,
        mutation_score=mutation_score,
        mutation_score_adjusted=mutation_score_adjusted,
        test_strength=test_strength,
        prioritized_surviving_mutants=survivor_objects,
        taxonomy_version=TAXONOMY_VERSION,
        dataset_schema_version=DATASET_SCHEMA_VERSION,
        ml_model_loaded=_ML_MODEL is not None,
        explanation_summary=(
            f"{n_active} active survivor(s) out of {total} total mutants "
            f"({uncovered} uncovered, {equivalent_filtered} suspected-equivalent, "
            f"{killed} killed). {model_note}. {pattern_summary}"
        ),
    )


def _write_run_report(run_id: str, module_key: str, suite: str,
                      filtered: List[Dict], attributions: List[Dict],
                      unresolved: int, unclassified: int, total: int) -> None:
    """Per-run audit trail.

    Everything the Evidence Object cannot carry without bloating an
    inter-service contract goes here: which mutants the triage filter removed
    and why, and which feature drove each ML score. The first is what makes
    "we never silently drop evidence" checkable rather than merely asserted.
    """
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": run_id,
            "module_key": module_key,
            "suite": suite,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "taxonomy_version": TAXONOMY_VERSION,
            "quality": {
                "total_mutants": total,
                "unresolved_line_mutants": unresolved,
                "unclassified_mutants": unclassified,
                "unclassified_rate": round(unclassified / total, 4) if total else 0.0,
            },
            "equivalent_suspected": filtered,
            "model_attributions": attributions,
        }
        (REPORTS_DIR / f"run_{run_id}.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
    except OSError:
        pass  # an unwritable reports dir must not fail an otherwise-good run
