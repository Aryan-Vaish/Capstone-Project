"""
Mutant Generator -- wraps mutmut 3.x
======================================
IMPORTANT: mutmut 3.x dropped the old --paths-to-mutate / --tests-dir CLI
flags entirely. It now reads a setup.cfg [mutmut] section. Verified live
against mutmut 3.7.0 -- do not trust older tutorials/docs for the CLI shape.

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

We parse that diff to recover the line number and guess an operator
category. The guess is heuristic -- mutmut doesn't label operators using
our taxonomy (conditional_boundary, negate_conditional, etc.), so we
pattern-match the before/after tokens ourselves.
"""
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class Mutant:
    mutant_id: str
    status: str                 # "killed" | "survived"
    function_name: str
    line_number: Optional[int] = None
    operator: str = "unknown"
    diff: str = ""


# Heuristic operator classifier: (regex on removed token, regex on added token) -> our taxonomy name
OPERATOR_RULES = [
    (r"(?<![><=!])>(?!=)", r">=", "conditional_boundary"),
    (r"(?<![><=!])<(?!=)", r"<=", "conditional_boundary"),
    (r">=", r">", "conditional_boundary"),
    (r"<=", r"<", "conditional_boundary"),
    (r"==", r"!=", "negate_conditional"),
    (r"!=", r"==", "negate_conditional"),
    (r"\+", r"-", "arithmetic_replacement"),
    (r"-", r"\+", "arithmetic_replacement"),
    (r"\*", r"/", "arithmetic_replacement"),
    (r"None", r"''", "null_check_removal"),
    (r"return ", r"return None", "return_value_replacement"),
]


def _guess_operator(diff_text: str) -> str:
    # Strip the leading diff marker character itself (the bug: keeping it made
    # every removed/added line start with literal "-"/"+", which trivially
    # matched the arithmetic_replacement rule regardless of actual content).
    removed = "\n".join(l[1:] for l in diff_text.splitlines() if l.startswith("-") and not l.startswith("---"))
    added = "\n".join(l[1:] for l in diff_text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    for rem_pat, add_pat, name in OPERATOR_RULES:
        if re.search(rem_pat, removed) and re.search(add_pat, added):
            return name
    return "unknown"


def _guess_line_number(diff_text: str) -> Optional[int]:
    """Walk the unified diff hunk, counting context + removed lines to find
    the actual source line of the first changed (-) line, rather than just
    returning the hunk's start line (which is wrong for any hunk >1 line)."""
    lines = diff_text.splitlines()
    hunk_start = None
    offset = 0
    for l in lines:
        m = re.match(r"@@ -(\d+),", l)
        if m:
            hunk_start = int(m.group(1))
            offset = 0
            continue
        if hunk_start is None:
            continue
        if l.startswith("-") and not l.startswith("---"):
            return hunk_start + offset
        if l.startswith("+") and not l.startswith("+++"):
            continue  # added lines don't consume an original-file line number
        offset += 1  # context line
    return hunk_start


def setup_mutmut_config(work_dir: Path, source_file: str, tests_dir: str) -> None:
    """Writes the setup.cfg mutmut 3.x actually reads (verified live)."""
    cfg = work_dir / "setup.cfg"
    cfg.write_text(
        f"[mutmut]\n"
        f"paths_to_mutate={source_file}\n"
        f"tests_dir={tests_dir}\n"
        f"runner=python -m pytest\n"
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


def collect_results(work_dir: Path) -> List[Mutant]:
    """Parses `mutmut results --all true` + `mutmut show <id>` per mutant."""
    result = subprocess.run(
        ["mutmut", "results", "--all", "true"],
        cwd=str(work_dir), capture_output=True, text=True,
    )
    mutants: List[Mutant] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        m = re.match(r"([\w\.]+): (killed|survived)", line)
        if not m:
            continue
        mutant_id, status = m.group(1), m.group(2)
        function_name = mutant_id.rsplit("__mutmut_", 1)[0]

        show = subprocess.run(
            ["mutmut", "show", mutant_id],
            cwd=str(work_dir), capture_output=True, text=True,
        )
        diff_text = show.stdout

        mutants.append(Mutant(
            mutant_id=mutant_id,
            status=status,
            function_name=function_name,
            line_number=_guess_line_number(diff_text),
            operator=_guess_operator(diff_text),
            diff=diff_text,
        ))
    return mutants


def generate_and_run(work_dir: Path, source_file: str, tests_dir: str, timeout: int = 300) -> List[Mutant]:
    """Full pipeline: configure -> run -> collect. work_dir must contain source_file and its tests."""
    setup_mutmut_config(work_dir, source_file, tests_dir)
    run_mutmut(work_dir, timeout=timeout)
    return collect_results(work_dir)
