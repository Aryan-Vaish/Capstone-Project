"""
Mutation Evidence Object -- shared contract (schema v1)
=========================================================
Owner: Mutation Testing module, but IMPORTED by Governance and the
Orchestrator too. Changes here need team sign-off since it's the
inter-service contract.
"""
from typing import List, Optional
from pydantic import BaseModel


class SurvivingMutant(BaseModel):
    mutant_id: str
    operator: str
    location: str               # "file.py:line"
    ml_priority_score: Optional[float] = None
    missing_edge_case: Optional[str] = None
    suggested_test_stub: Optional[str] = None


class MutationEvidenceObject(BaseModel):
    module: str = "mutation_testing"
    schema_version: str = "1.0"
    run_id: str
    generated_at: str
    mutation_score: float
    total_mutants: int
    killed: int
    survived: int
    uncovered_mutants: int = 0          # mutants on lines no stable test touches
    equivalent_mutants_filtered: int = 0
    prioritized_surviving_mutants: List[SurvivingMutant] = []
    explanation_summary: str = ""


class MutationRunRequest(BaseModel):
    run_id: Optional[str] = None
    source_file: str            # path to the single module being mutated
    tests_dir: str               # path to the dir containing its tests
    stable_test_ids: Optional[List[str]] = None   # from Flaky Detection, optional for now
