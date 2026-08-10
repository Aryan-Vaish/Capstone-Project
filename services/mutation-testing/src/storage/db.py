"""
Mutant Record Storage (dataset schema v2)
============================================
Persists every mutant from every pipeline run into SQLite with its extracted
features and outcome. This IS the training set for ml/train.py -- assembled as a
side effect of normal runs, so no manual labelling is ever needed.

WHAT CHANGED IN v2, AND WHY
---------------------------
1. `dataset_schema_version` on every row. v1 had no version, so when the
   taxonomy or the feature set changed there was no way to keep old and new rows
   apart -- training silently mixed incompatible vectors. train.py now filters
   on this column, which makes that class of bug structurally impossible.

2. Real module identity. v1 stored `source_path.name`, which was "module.py" for
   every corpus module because they were all named that. 153 of 155 rows shared
   one value. That made module-aware train/test splitting impossible (so the
   reported 73.3% validation accuracy was measured with same-module rows on both
   sides) and made held-out benchmarking impossible.

3. A uniqueness constraint. v1 appended unconditionally, so every `pytest` run
   re-inserted the same mutants: the 155 v1 rows were 58 distinct mutants, one
   of them stored 8 times. Re-collection now upserts instead of duplicating.

4. Triage provenance. `arid_filter` computes a confidence and a reason code and
   v1 threw both away, keeping only the status -- despite the module's own
   docstring promising the decision "stays auditable". They are columns now.

5. `DB_PATH` resolves per call from MUTATION_DB_PATH rather than being fixed at
   import. That is what lets the test suite point at a temp file instead of
   writing into the real training set (which is where those 8x duplicates and
   the stray `test.mutant.1` row came from).
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional

DATASET_SCHEMA_VERSION = "v2"

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "storage" / "mutants.db"


def db_path() -> Path:
    """Resolved at call time so tests can redirect writes via the environment."""
    override = os.environ.get("MUTATION_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS mutants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- provenance
    dataset_schema_version TEXT NOT NULL,
    taxonomy_version TEXT,
    pipeline_git_sha TEXT,
    corpus_git_sha TEXT,
    run_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,

    -- identity
    module_key TEXT NOT NULL,      -- 'corpus/02_discount_pricing/discount_pricing.py'
    module_slug TEXT,              -- '02_discount_pricing'
    suite TEXT,                    -- 'weak' | 'strong'
    mutant_id TEXT NOT NULL,
    function_name TEXT,
    class_name TEXT,

    -- what the mutation was
    operator TEXT,
    operator_family TEXT,
    line_number INTEGER,
    line_resolution TEXT,          -- content_unique | content_disambiguated | unresolved

    -- structural features (function-scoped in v2, not file-scoped)
    func_complexity INTEGER,
    func_loc INTEGER,
    func_num_params INTEGER,
    func_num_returns INTEGER,
    func_num_branches INTEGER,
    node_depth_at_line INTEGER,
    rel_position_in_func REAL,
    in_conditional INTEGER,
    in_loop INTEGER,
    in_exception INTEGER,
    covered INTEGER,
    n_covering_tests INTEGER,

    -- triage decision, kept auditable
    triage_status TEXT,
    triage_confidence REAL,
    triage_reason_code TEXT,

    outcome TEXT,                  -- killed | active_survivor | uncovered | equivalent_suspected

    UNIQUE (dataset_schema_version, module_key, suite, mutant_id)
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mutants_version ON mutants(dataset_schema_version)",
    "CREATE INDEX IF NOT EXISTS idx_mutants_module ON mutants(module_key)",
    "CREATE INDEX IF NOT EXISTS idx_mutants_outcome ON mutants(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_mutants_run ON mutants(run_id)",
]

_COLUMNS = [
    "dataset_schema_version", "taxonomy_version", "pipeline_git_sha", "corpus_git_sha",
    "run_id", "module_key", "module_slug", "suite", "mutant_id", "function_name",
    "class_name", "operator", "operator_family", "line_number", "line_resolution",
    "func_complexity", "func_loc", "func_num_params", "func_num_returns",
    "func_num_branches", "node_depth_at_line", "rel_position_in_func",
    "in_conditional", "in_loop", "in_exception", "covered", "n_covering_tests",
    "triage_status", "triage_confidence", "triage_reason_code", "outcome",
]


def init_db(path: Optional[Path] = None) -> None:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as conn:
        conn.execute(SCHEMA)
        for stmt in INDEXES:
            conn.execute(stmt)


@contextmanager
def connection(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Single initialised connection.

    v1 called init_db() at the top of every public function and opened a fresh
    connection per insert -- 155 connect/commit/close cycles for one run, which
    does not scale to a 20-module corpus."""
    target = path or db_path()
    init_db(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_record(
    run_id: str,
    module_key: str,
    mutant_id: str,
    features: Dict,
    outcome: str,
    *,
    module_slug: str = "",
    suite: str = "weak",
    function_name: str = "",
    class_name: Optional[str] = None,
    line_resolution: str = "",
    triage_status: str = "",
    triage_confidence: Optional[float] = None,
    triage_reason_code: str = "",
    taxonomy_version: str = "",
    pipeline_git_sha: str = "",
    corpus_git_sha: str = "",
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """Upsert one mutant record.

    INSERT OR REPLACE against the uniqueness constraint, so re-running a
    collection over the same corpus corrects rows in place instead of appending
    near-duplicates."""
    row = {
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "taxonomy_version": taxonomy_version,
        "pipeline_git_sha": pipeline_git_sha,
        "corpus_git_sha": corpus_git_sha,
        "run_id": run_id,
        "module_key": module_key,
        "module_slug": module_slug,
        "suite": suite,
        "mutant_id": mutant_id,
        "function_name": function_name,
        "class_name": class_name,
        "line_resolution": line_resolution,
        "triage_status": triage_status,
        "triage_confidence": triage_confidence,
        "triage_reason_code": triage_reason_code,
        "outcome": outcome,
    }
    for key in ("operator", "operator_family", "line_number", "func_complexity",
                "func_loc", "func_num_params", "func_num_returns", "func_num_branches",
                "node_depth_at_line", "rel_position_in_func", "in_conditional",
                "in_loop", "in_exception", "covered", "n_covering_tests"):
        row[key] = features.get(key)

    placeholders = ", ".join("?" for _ in _COLUMNS)
    sql = (f"INSERT OR REPLACE INTO mutants ({', '.join(_COLUMNS)}) "
           f"VALUES ({placeholders})")
    values = [row.get(c) for c in _COLUMNS]

    if conn is not None:
        conn.execute(sql, values)
        return
    with connection() as own:
        own.execute(sql, values)


def fetch_all_records(schema_version: Optional[str] = DATASET_SCHEMA_VERSION) -> List[Dict]:
    """Rows for training. Filtered to the current schema version by default so a
    stale-format row can never silently enter a training set."""
    with connection() as conn:
        if schema_version is None:
            rows = conn.execute("SELECT * FROM mutants").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mutants WHERE dataset_schema_version = ?",
                (schema_version,),
            ).fetchall()
    return [dict(r) for r in rows]


def fetch_training_population(schema_version: str = DATASET_SCHEMA_VERSION) -> List[Dict]:
    """The rows the model should actually train on.

    Restricted to match inference conditions: `predict_priority` is only ever
    called on covered, non-arid survivors of the weak suite. Training on
    uncovered mutants would be worse than useless -- `covered = 0` implies the
    line never executes, which implies it cannot be killed, so the label becomes
    deterministically readable from a single feature and the model banks free
    accuracy on a tautology it will never face in production.
    """
    with connection() as conn:
        rows = conn.execute(
            """SELECT * FROM mutants
               WHERE dataset_schema_version = ?
                 AND covered = 1
                 AND outcome != 'uncovered'
                 AND (triage_status IS NULL OR triage_status != 'equivalent_suspected')
                 AND suite = 'weak'""",
            (schema_version,),
        ).fetchall()
    return [dict(r) for r in rows]


def count_records(schema_version: Optional[str] = None) -> int:
    with connection() as conn:
        if schema_version is None:
            return conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM mutants WHERE dataset_schema_version = ?",
            (schema_version,),
        ).fetchone()[0]


def distinct_modules(schema_version: str = DATASET_SCHEMA_VERSION) -> List[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT module_key FROM mutants WHERE dataset_schema_version = ?",
            (schema_version,),
        ).fetchall()
    return [r[0] for r in rows]
