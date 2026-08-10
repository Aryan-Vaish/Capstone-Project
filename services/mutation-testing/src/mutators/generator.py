"""
Mutant Generator -- wraps mutmut 3.x
======================================
IMPORTANT: mutmut 3.x dropped the old --paths-to-mutate / --tests-dir CLI
flags entirely. It now reads a setup.cfg [mutmut] section. Verified live
against mutmut 3.7.0 -- do not trust older tutorials/docs for the CLI shape.

mutmut has no native Windows support; every call in this module must run under
WSL/Linux. See services/mutation-testing/README.md for the environment setup.

Real, tested output of `mutmut results --all true`:
    module.x_withdraw__mutmut_1: survived
    module.x_withdraw__mutmut_2: survived
    ...
    module.x_withdraw__mutmut_6: killed

Real, tested output of `mutmut show <mutant_id>`:
    # module.x_withdraw__mutmut_1: survived
    --- module.py
    +++ module.py
    @@ -1,4 +1,4 @@
     def withdraw(balance: float, amount: float) -> float:
    -    if amount > balance:
    +    if amount >= balance:
             raise ValueError("Insufficient funds")
        return balance - amount

TWO THINGS THAT DIFF DOES NOT TELL YOU, AND V1 GOT WRONG
--------------------------------------------------------
1. The hunk header is relative to the *isolated function*, not the file --
   mutmut renders `cst.Module([function])` and diffs that. `mutators/
   line_resolver.py` maps it back to a real file line by content.
2. mutmut labels nothing with an operator name, so we classify the diff
   ourselves. `mutators/classifier.py` does that against taxonomy v2, which is
   transcribed from mutmut's own dispatch table rather than guessed.

Both were split into their own modules because they are the parts most likely
to need iteration, and neither needs mutmut (or WSL) to be tested.
"""
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from mutators.classifier import classify_diff
from mutators.line_resolver import (
    FunctionRef,
    FunctionSpan,
    build_function_index,
    parse_mutant_id,
    resolve_mutant_line,
)
from mutators.taxonomy import OTHER


@dataclass
class Mutant:
    mutant_id: str
    status: str                        # "killed" | "survived"
    function_name: str                 # bare name, e.g. "withdraw"
    line_number: Optional[int] = None  # absolute line in the ORIGINAL source
    operator: str = OTHER
    diff: str = ""
    class_name: Optional[str] = None
    qualified_name: str = ""           # "Class.method" or "function"
    resolution_method: str = ""        # how line_number was determined
    classification_detail: str = ""    # token substitution, for the unclassified log


def setup_mutmut_config(work_dir: Path, source_file: str, tests_dir: str) -> None:
    """Writes the setup.cfg mutmut 3.x actually reads (verified live)."""
    cfg = work_dir / "setup.cfg"
    cfg.write_text(
        f"[mutmut]\n"
        f"paths_to_mutate={source_file}\n"
        f"tests_dir={tests_dir}\n"
        f"runner=python -m pytest\n",
        encoding="utf-8",
    )


def run_mutmut(work_dir: Path, timeout: int = 300) -> None:
    """Runs `mutmut run` in work_dir. Non-zero exit is normal (means mutants survived)."""
    subprocess.run(
        ["mutmut", "run"],
        cwd=str(work_dir),
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def _show_mutant(work_dir: Path, mutant_id: str) -> str:
    result = subprocess.run(
        ["mutmut", "show", mutant_id],
        cwd=str(work_dir), capture_output=True, text=True,
    )
    return result.stdout


def collect_results(work_dir: Path, original_source: Path,
                    max_workers: int = 8) -> List[Mutant]:
    """Parses `mutmut results --all true`, then one `mutmut show` per mutant.

    `original_source` is the pre-mutation file: line numbers are resolved
    against it, not against the copy inside work_dir, so callers get
    coordinates that mean something outside the temp directory.

    The `show` calls are the dominant cost -- one subprocess per mutant, and a
    20-module corpus run is well into four figures of them -- so they are issued
    concurrently. Ordering is restored afterwards to keep runs reproducible.
    """
    result = subprocess.run(
        ["mutmut", "results", "--all", "true"],
        cwd=str(work_dir), capture_output=True, text=True,
    )

    entries: List[tuple] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        # mangled names may contain U+01C1 for methods, so \w is not enough
        m = re.match(r"([^\s:]+): (killed|survived)", line)
        if m:
            entries.append((m.group(1), m.group(2)))

    if not entries:
        return []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        diffs = list(pool.map(lambda e: _show_mutant(work_dir, e[0]), entries))

    source_text = original_source.read_text(encoding="utf-8")
    source_lines = source_text.splitlines()
    function_index: Dict[str, FunctionSpan] = build_function_index(source_text)

    mutants: List[Mutant] = []
    for (mutant_id, status), diff_text in zip(entries, diffs):
        classification = classify_diff(diff_text)
        line_number, ref, method = resolve_mutant_line(
            original_source,
            mutant_id,
            diff_text,
            removed_line=classification.removed_line,
            function_index=function_index,
            source_lines=source_lines,
        )
        mutants.append(Mutant(
            mutant_id=mutant_id,
            status=status,
            function_name=ref.function_name,
            class_name=ref.class_name,
            qualified_name=ref.qualified,
            line_number=line_number,
            operator=classification.operator,
            diff=diff_text,
            resolution_method=method,
            classification_detail=classification.detail,
        ))
    return mutants


def generate_and_run(work_dir: Path, source_file: str, tests_dir: str,
                     original_source: Path, timeout: int = 300) -> List[Mutant]:
    setup_mutmut_config(work_dir, source_file, tests_dir)
    run_mutmut(work_dir, timeout=timeout)
    return collect_results(work_dir, original_source)
