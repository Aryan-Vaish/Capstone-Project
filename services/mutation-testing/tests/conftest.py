"""
Test isolation.

THE BUG THIS FIXES
------------------
`storage/db.py` wrote to a path fixed at import time, so every test that touched
the pipeline wrote into the REAL training database. Three tests in test_main.py
each POST /mutation/run, so a single `pytest` invocation appended ~18 rows to the
production dataset.

Measured on dataset v1: 155 stored rows were only 58 distinct mutants, with
`module.x_withdraw__mutmut_1` present 8 times, plus a stray `test.mutant.1` row
from test_storage_insert_and_fetch. Because `train_test_split` shuffles rows
rather than mutants, byte-identical duplicates landed on both sides of the
split -- so the reported 73.3% "validation accuracy" was partly measured on
memorised training rows.

Pointing MUTATION_DB_PATH at a temp file for the whole session makes the test
suite incapable of touching the real dataset.
"""
import os
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
SRC = SERVICE_ROOT / "src"
REPO_ROOT = SERVICE_ROOT.parents[1]

for path in (str(REPO_ROOT), str(SRC)):
    if path not in sys.path:
        sys.path.insert(0, path)


@pytest.fixture(scope="session", autouse=True)
def isolated_database(tmp_path_factory):
    """Redirect all DB writes to a session-scoped temp file.

    autouse + session scope so it applies before any test imports the app;
    `db_path()` reads the environment on every call, so this is sufficient
    without monkeypatching module state.
    """
    tmp_db = tmp_path_factory.mktemp("mutation_db") / "test_mutants.db"
    previous = os.environ.get("MUTATION_DB_PATH")
    os.environ["MUTATION_DB_PATH"] = str(tmp_db)
    yield tmp_db
    if previous is None:
        os.environ.pop("MUTATION_DB_PATH", None)
    else:
        os.environ["MUTATION_DB_PATH"] = previous


@pytest.fixture(scope="session")
def service_root() -> Path:
    """Tests reference corpus paths relative to the service root rather than the
    process CWD, so `pytest` works from anywhere (v1's tests only passed when
    run from services/mutation-testing/)."""
    return SERVICE_ROOT
