"""
Mutant Record Storage
========================
Persists every mutant produced by every /mutation/run call into SQLite,
with its extracted features and outcome (killed/survived/uncovered/
equivalent_suspected). This IS the training dataset for ml/train.py --
built for free as a side effect of normal pipeline runs, no manual
labeling needed, per the original plan's Step 6 reasoning.

DB file lives at services/mutation-testing/storage/mutants.db (gitignored
-- it's generated data, not source code, so it shouldn't be committed).
"""
import sqlite3
from pathlib import Path
from typing import Dict

DB_PATH = Path(__file__).resolve().parents[2] / "storage" / "mutants.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS mutants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    module TEXT,
    mutant_id TEXT,
    operator TEXT,
    line_number INTEGER,
    complexity INTEGER,
    ast_depth INTEGER,
    in_conditional INTEGER,
    in_loop INTEGER,
    in_exception INTEGER,
    covered INTEGER,
    outcome TEXT,          -- 'killed' | 'active_survivor' | 'uncovered' | 'equivalent_suspected'
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    conn.commit()
    conn.close()


def insert_record(run_id: str, module: str, mutant_id: str, features: Dict, outcome: str) -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO mutants
           (run_id, module, mutant_id, operator, line_number, complexity,
            ast_depth, in_conditional, in_loop, in_exception, covered, outcome)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, module, mutant_id, features["operator"], features["line_number"],
         features["complexity"], features["ast_depth"], features["in_conditional"],
         features["in_loop"], features["in_exception"], features["covered"], outcome),
    )
    conn.commit()
    conn.close()


def fetch_all_records():
    """Returns list of dict rows -- used by ml/train.py to build the training set."""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM mutants").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_records() -> int:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM mutants").fetchone()[0]
    conn.close()
    return n
