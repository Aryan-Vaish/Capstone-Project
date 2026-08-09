"""
Execution Harness
===================
Isolates each mutation run in its own temp working directory (mutmut writes
a mutants/ cache folder + setup.cfg alongside the source, so running
concurrent requests against the same corpus folder would corrupt each
other's state without this).
"""
import shutil
import tempfile
from pathlib import Path
from typing import List

from mutators.generator import Mutant, generate_and_run


def run_mutation_analysis(source_path: Path, tests_dir_path: Path, timeout: int = 300) -> List[Mutant]:
    """
    source_path: path to the single .py file to mutate
    tests_dir_path: directory containing that module's test file(s)
    Copies both into an isolated temp dir, runs mutmut there, cleans up after.
    """
    with tempfile.TemporaryDirectory(prefix="mutation_run_") as tmp:
        work_dir = Path(tmp)
        shutil.copy(source_path, work_dir / source_path.name)
        for test_file in tests_dir_path.glob("test_*.py"):
            shutil.copy(test_file, work_dir / test_file.name)

        mutants = generate_and_run(work_dir, source_path.name, ".", timeout=timeout)
        return mutants
