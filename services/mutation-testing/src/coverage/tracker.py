"""
Coverage Tracker
==================
Determines which lines of the ORIGINAL (unmutated) source are actually
exercised by the stable test suite. This runs once per mutation-testing
request, BEFORE looking at mutants, using Python's own `coverage` package.

Why this matters: a mutant that survives on a line NO test touches is a
coverage gap ("nobody wrote a test for this line at all"), not a test-quality
gap ("the test exists but doesn't check the right thing"). Conflating the
two makes the explainability layer (later step) generate wrong advice --
"you're missing an edge case test" is the wrong message when the real
problem is "you have zero tests for this line".
"""
import subprocess
import sys
from pathlib import Path
from typing import Set


def get_covered_lines(work_dir: Path, source_file: str) -> Set[int]:
    """
    Runs the test suite in work_dir under coverage, against the ORIGINAL
    (unmutated) source_file, and returns the set of line numbers that were
    actually executed by at least one test.
    """
    subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source", source_file.replace(".py", ""),
         "-m", "pytest", "-q"],
        cwd=str(work_dir), capture_output=True, text=True,
    )
    report = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "-"],
        cwd=str(work_dir), capture_output=True, text=True,
    )
    import json
    try:
        data = json.loads(report.stdout)
    except json.JSONDecodeError:
        return set()

    module_key = source_file  # coverage keys files by their relative path
    for fname, filedata in data.get("files", {}).items():
        if fname == source_file or fname.endswith("/" + source_file):
            return set(filedata["executed_lines"])
    return set()
