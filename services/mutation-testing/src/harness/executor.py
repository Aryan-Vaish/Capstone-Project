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
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mutators.generator import Mutant, generate_and_run
from mutation_coverage.tracker import CoverageError, get_coverage_detail


def run_mutation_analysis(
    source_path: Path,
    tests_dir_path: Path,
    timeout: int = 300,
    with_contexts: bool = True,
) -> Tuple[List[Mutant], Set[int], Dict[int, int]]:
    """
    source_path: the single .py file to mutate
    tests_dir_path: directory holding that module's test file(s)

    Copies both into an isolated temp dir, runs coverage THEN mutmut there, and
    cleans up. Returns (mutants, covered_lines, covering_test_counts).

    `with_contexts` enables coverage's dynamic contexts so we learn how many
    distinct tests touch each line. It is best-effort: if the context report is
    unavailable the run still proceeds with plain line coverage, since the
    per-line test count is a useful feature but not a correctness requirement.
    """
    with tempfile.TemporaryDirectory(prefix="mutation_run_") as tmp:
        work_dir = Path(tmp)
        shutil.copy(source_path, work_dir / source_path.name)
        for test_file in tests_dir_path.glob("test_*.py"):
            shutil.copy(test_file, work_dir / test_file.name)

        try:
            covered_lines, covering_counts = get_coverage_detail(
                work_dir, source_path.name, with_contexts=with_contexts
            )
        except CoverageError:
            if not with_contexts:
                raise
            covered_lines, covering_counts = get_coverage_detail(
                work_dir, source_path.name, with_contexts=False
            )
        # `original_source` is the caller's file, not the temp copy: mutant line
        # numbers must be expressed in coordinates that still mean something
        # once this directory is gone, and must match the coverage data above.
        mutants = generate_and_run(
            work_dir, source_path.name, ".",
            original_source=source_path, timeout=timeout,
        )
        return mutants, covered_lines, covering_counts
