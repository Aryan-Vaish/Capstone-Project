"""
Execution Harness
===================
Isolates each mutation run in its own temp working directory (mutmut writes
a mutants/ cache folder + setup.cfg alongside the source, so running
concurrent requests against the same corpus folder would corrupt each
other's state without this).

Day 2 update: also runs coverage tracking in the SAME temp directory,
before mutation testing starts, so mutant survivors can be cross-referenced
against real coverage data. This is why coverage happens inside this
function rather than as a separate step in main.py -- it needs the same
isolated copy of the source+tests that mutation testing uses.
"""
import shutil
import sys
import tempfile
from pathlib import Path
from typing import List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mutators.generator import Mutant, generate_and_run
from coverage.tracker import get_covered_lines


def run_mutation_analysis(source_path: Path, tests_dir_path: Path, timeout: int = 300) -> Tuple[List[Mutant], Set[int]]:
    """
    source_path: path to the single .py file to mutate
    tests_dir_path: directory containing that module's test file(s)
    Copies both into an isolated temp dir, runs coverage THEN mutmut there,
    cleans up after. Returns (mutants, covered_lines).
    """
    with tempfile.TemporaryDirectory(prefix="mutation_run_") as tmp:
        work_dir = Path(tmp)
        shutil.copy(source_path, work_dir / source_path.name)
        for test_file in tests_dir_path.glob("test_*.py"):
            shutil.copy(test_file, work_dir / test_file.name)

        covered_lines = get_covered_lines(work_dir, source_path.name)
        mutants = generate_and_run(work_dir, source_path.name, ".", timeout=timeout)
        return mutants, covered_lines
