"""
Mutation Testing Evidence Object -- inter-service contract.

Consumed by Governance (trust scoring) and the Orchestrator. Because other
services depend on these field names and meanings, schema changes here are
ADDITIVE: `mutation_score` keeps exactly the meaning it had in 1.0 and the new
numbers arrive alongside it under new names.

SCORE DEFINITIONS (new in 1.1)
------------------------------
Reporting a single "mutation score" is ambiguous, and the ambiguity flatters the
tool. Three numbers are published instead, using Pitest's vocabulary so they are
comparable to published results:

  mutation_score           killed / total
                           "mutation coverage". Uncovered mutants stay in the
                           denominator on purpose -- an untested line is a real
                           test gap, and excluding it is precisely the inflation
                           this project exists to avoid.

  mutation_score_adjusted  killed / (total - equivalent_suspected)
                           the classical mutation score, with mutants we believe
                           are equivalent removed. Equivalent mutants cannot be
                           killed by any test, so leaving them in permanently
                           caps the achievable score below 100%.

  test_strength            killed / (killed + active_survivors)
                           suite quality on the code the suite actually reaches.
                           Ignores coverage gaps, so it answers "where the tests
                           do run, how good are they?"

All three are emitted every run, with their formulas in `score_definitions`, so
no downstream reader has to guess which one a number is.
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1"

SCORE_DEFINITIONS: Dict[str, str] = {
    "mutation_score": "killed / total_mutants",
    "mutation_score_adjusted": "killed / (total_mutants - equivalent_mutants_filtered)",
    "test_strength": "killed / (killed + active_survivors)",
}


class SurvivingMutant(BaseModel):
    mutant_id: str
    operator: str                                   # taxonomy v2 fine-grained name
    operator_family: str = ""                       # coarse family used by the model
    location: str                                   # "file.py:line", absolute file line
    function_name: str = ""
    ml_priority_score: Optional[float] = None       # P(killed); None when no model
    missing_edge_case: Optional[str] = None         # templated NLG, never model-gated
    suggested_test_stub: Optional[str] = None
    line_resolution: str = ""                       # how the line number was determined


class MutationEvidenceObject(BaseModel):
    module: str = "mutation_testing"
    schema_version: str = SCHEMA_VERSION
    run_id: str
    generated_at: str

    # --- counts ---
    total_mutants: int
    killed: int
    survived: int
    uncovered_mutants: int = 0
    equivalent_mutants_filtered: int = 0
    active_survivors: int = 0
    unresolved_line_mutants: int = 0     # mutants whose source line could not be located
    unclassified_mutants: int = 0        # operator == 'other'; a data-quality signal

    # --- scores ---
    mutation_score: float
    mutation_score_adjusted: float = 0.0
    test_strength: float = 0.0
    score_definitions: Dict[str, str] = Field(default_factory=lambda: dict(SCORE_DEFINITIONS))

    prioritized_surviving_mutants: List[SurvivingMutant] = []
    explanation_summary: str = ""

    # --- provenance ---
    taxonomy_version: str = ""
    dataset_schema_version: str = ""
    ml_model_loaded: bool = False


class MutationRunRequest(BaseModel):
    run_id: Optional[str] = None
    source_file: str
    tests_dir: str
    module_id: Optional[str] = None      # overrides the derived module_key
    suite: str = "weak"                  # 'weak' (shipped suite) | 'strong' (oracle suite)

    # Supplied by Flaky Detection so known-flaky tests can be excluded from the
    # kill decision. NOT YET CONSUMED -- declared here to fix the contract shape
    # early, but nothing reads it. Do not report this as implemented.
    stable_test_ids: Optional[List[str]] = None
