"""
Schema-migration tests.

THE BLIND SPOT THESE CLOSE
--------------------------
Day 7 added `removed_line` and `added_line` to the mutants table without a
migration. `init_db()` runs `CREATE TABLE IF NOT EXISTS`, which is a no-op once
the table exists, so a database created by an earlier version never gained the
columns -- and every INSERT then failed with:

    OperationalError: table mutants has no column named removed_line

All 20 module x suite runs of a collaborator's collection died on it, while 183
tests passed on the same machine. The reason no test caught it is structural:
`tests/conftest.py` points MUTATION_DB_PATH at a fresh `tmp_path_factory` file
for every session, so `init_db` always builds the table from scratch at the
current schema. **No other test in the suite ever opens a pre-existing
database.**

So these tests deliberately bypass that fixture: each builds its own database
file and passes an explicit path to `init_db(path)` / `connection(path)`. That
bypass is the entire point of the file -- a migration bug is invisible to any
test that starts from an empty directory.
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from storage import db as dbmod  # noqa: E402
from storage.db import SCHEMA, _expected_columns, _migrate, init_db  # noqa: E402


def columns_of(path: Path) -> list:
    conn = sqlite3.connect(path)
    try:
        return [r[1] for r in conn.execute("PRAGMA table_info(mutants)")]
    finally:
        conn.close()


def make_legacy_db(path: Path, drop=("removed_line", "added_line")) -> None:
    """A database as an older version of the code would have created it."""
    schema = SCHEMA
    for col in drop:
        # trailing `-- ...` comments are part of the declaration line
        schema = re.sub(rf"\n\s*{col} (?:TEXT|INTEGER|REAL),[^\n]*", "", schema)
        assert col not in schema, f"could not remove {col} from SCHEMA"
    conn = sqlite3.connect(path)
    try:
        conn.execute(schema)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------

def test_expected_columns_derived_from_schema():
    """The expected set comes from SCHEMA itself, not a second hand-kept list --
    a duplicate declaration is how the schema drifted in the first place."""
    expected = _expected_columns()
    assert "removed_line" in expected
    assert "added_line" in expected
    # every data column the insert names must be creatable
    for col in dbmod._COLUMNS:
        assert col in expected, f"_COLUMNS names {col!r} but SCHEMA does not declare it"


def test_migration_adds_missing_columns(tmp_path):
    """Generic on purpose: it keeps working for future additions rather than
    pinning today's two."""
    db = tmp_path / "legacy.db"
    make_legacy_db(db)
    before = columns_of(db)
    assert "removed_line" not in before

    init_db(db)

    after = columns_of(db)
    assert set(_expected_columns()) <= set(after)
    assert len(after) == len(before) + 2


def test_migration_preserves_existing_rows(tmp_path):
    """Migration must never be destructive -- a collaborator's 670-row dataset
    represents a 30-60 minute Linux-only collection run."""
    db = tmp_path / "legacy.db"
    make_legacy_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        """INSERT INTO mutants (dataset_schema_version, module_key, mutant_id,
                                suite, outcome, operator)
           VALUES ('v2', 'corpus/01_x/x.py', 'x.f__mutmut_1', 'weak', 'killed',
                   'comparison_boundary')""")
    conn.commit()
    conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM mutants WHERE mutant_id = 'x.f__mutmut_1'").fetchone()
    conn.close()
    assert row is not None, "migration destroyed pre-existing data"
    assert row["outcome"] == "killed"
    assert row["operator"] == "comparison_boundary"
    assert row["removed_line"] is None      # new column, no value for old rows


def test_insert_after_migration_succeeds(tmp_path, monkeypatch):
    """The exact failure the collaborator hit, end to end."""
    db = tmp_path / "legacy.db"
    make_legacy_db(db)
    monkeypatch.setenv("MUTATION_DB_PATH", str(db))

    features = {k: 0 for k in (
        "line_number", "func_complexity", "func_loc", "func_num_params",
        "func_num_returns", "func_num_branches", "node_depth_at_line",
        "rel_position_in_func", "in_conditional", "in_loop", "in_exception",
        "covered", "n_covering_tests")}
    features.update(operator="string_literal_change", operator_family="constant")

    # would raise OperationalError before the migration existed
    dbmod.insert_record(
        "run-1", "corpus/01_x/x.py", "x.f__mutmut_2", features, "killed",
        suite="weak", removed_line='    return "A"', added_line='    return "XXAXX"',
    )

    rows = [r for r in dbmod.fetch_all_records() if r["mutant_id"] == "x.f__mutmut_2"]
    assert len(rows) == 1
    assert rows[0]["removed_line"] == '    return "A"'
    assert rows[0]["added_line"] == '    return "XXAXX"'


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    make_legacy_db(db)

    init_db(db)
    first = columns_of(db)
    init_db(db)
    second = columns_of(db)

    assert first == second

    conn = sqlite3.connect(db)
    try:
        assert _migrate(conn, announce=False) == [], "second migration added columns"
    finally:
        conn.close()


def test_fresh_database_needs_no_migration(tmp_path):
    """The new-database path still yields the full schema in one step, so the
    migration never fires for a clean checkout."""
    db = tmp_path / "fresh.db"
    init_db(db)
    assert set(columns_of(db)) == set(_expected_columns())

    conn = sqlite3.connect(db)
    try:
        assert _migrate(conn, announce=False) == []
    finally:
        conn.close()


def test_migration_reports_which_columns_it_added(tmp_path, capsys):
    """An operator must see that their database changed, not discover it later."""
    db = tmp_path / "legacy.db"
    make_legacy_db(db)
    init_db(db)
    out = capsys.readouterr().out
    assert "migrated mutants table" in out
    assert "removed_line" in out


def test_migration_on_a_table_missing_many_columns(tmp_path):
    """Not specific to the two Day-7 columns."""
    db = tmp_path / "legacy.db"
    make_legacy_db(db, drop=("removed_line", "added_line", "line_resolution"))
    assert "line_resolution" not in columns_of(db)

    init_db(db)

    after = set(columns_of(db))
    assert {"removed_line", "added_line", "line_resolution"} <= after


def test_missing_table_is_created_not_migrated(tmp_path):
    """An empty database file must go down the CREATE path cleanly."""
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    conn = sqlite3.connect(db)
    try:
        assert _migrate(conn, announce=False) == []   # nothing to migrate yet
    finally:
        conn.close()

    init_db(db)
    assert set(columns_of(db)) == set(_expected_columns())
