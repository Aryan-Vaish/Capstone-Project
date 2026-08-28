"""
Coverage Tracker
==================
Determines which lines of the ORIGINAL (unmutated) source are actually
exercised by the test suite. Runs once per mutation-testing request, before
any mutant is considered, using Python's own `coverage` package.

Why this matters: a mutant surviving on a line NO test touches is a coverage
gap ("nobody tested this line at all"), not a test-quality gap ("a test runs
this line but doesn't check the right thing"). Conflating the two makes the
explainability layer give wrong advice -- "you're missing an edge case" is the
wrong message when the real answer is "you have no tests for this line".

NOTE ON THE PACKAGE NAME
------------------------
This package is `mutation_coverage`, not `coverage`. It was renamed after a real
crash: SHAP depends on numba, numba imports the third-party `coverage` library,
and a local package named `coverage/` earlier on sys.path shadowed it.

FAIL LOUD, NOT SILENT
---------------------
This returned `set()` on any JSON parse failure, which is indistinguishable from
"the suite genuinely covers nothing" -- and an empty coverage set routes EVERY
mutant into the `uncovered` bucket, quietly emptying the active-survivor list
and producing a run that looks finished and is entirely wrong. Failures now
raise with the subprocess's stderr attached.
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Set, Tuple


class CoverageError(RuntimeError):
    """Coverage could not be measured. Never silently downgraded to 'nothing is
    covered', because that is a valid-looking result that corrupts the run."""


def _module_matches(fname: str, source_file: str) -> bool:
    """coverage reports paths with the platform separator; normalise before
    comparing (v1 only checked '/' and so never matched on Windows)."""
    normalised = fname.replace("\\", "/")
    target = source_file.replace("\\", "/")
    return normalised == target or normalised.endswith("/" + target)


def get_covered_lines(work_dir: Path, source_file: str,
                      with_contexts: bool = False) -> Set[int]:
    """Line numbers of `source_file` executed by at least one test.

    Line numbers are absolute within the file, which is what makes them
    comparable to the mutant line numbers produced by mutators/line_resolver.py.
    """
    covered, _ = get_coverage_detail(work_dir, source_file, with_contexts=with_contexts)
    return covered


def get_coverage_detail(work_dir: Path, source_file: str,
                        with_contexts: bool = False) -> Tuple[Set[int], Dict[int, int]]:
    """Returns (covered_lines, {line: number_of_distinct_tests_executing_it}).

    The per-line test count is the cheapest available proxy for how strongly a
    line is exercised -- closer to a causal predictor of "will a mutation here be
    caught" than any purely static feature. It requires coverage's dynamic
    contexts, so it is opt-in; without it the second element is empty.
    """
    module_name = source_file[:-3] if source_file.endswith(".py") else source_file

    if with_contexts:
        (work_dir / ".coveragerc").write_text(
            "[run]\ndynamic_context = test_function\n", encoding="utf-8"
        )

    run = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "--source", module_name,
         "-m", "pytest", "-q"],
        cwd=str(work_dir), capture_output=True, text=True,
    )

    report = subprocess.run(
        [sys.executable, "-m", "coverage", "json", "-o", "-"]
        + (["--show-contexts"] if with_contexts else []),
        cwd=str(work_dir), capture_output=True, text=True,
    )

    try:
        data = json.loads(report.stdout)
    except json.JSONDecodeError as exc:
        raise CoverageError(
            f"coverage produced no parseable JSON for {source_file}.\n"
            f"  coverage json stderr: {report.stderr.strip()[:500]}\n"
            f"  pytest run stderr:    {run.stderr.strip()[:500]}\n"
            f"  pytest run stdout:    {run.stdout.strip()[:500]}"
        ) from exc

    for fname, filedata in data.get("files", {}).items():
        if not _module_matches(fname, source_file):
            continue
        covered = set(filedata.get("executed_lines", []))
        counts: Dict[int, int] = {}
        contexts = filedata.get("contexts") or {}
        for line_str, ctx_list in contexts.items():
            try:
                counts[int(line_str)] = len({c for c in ctx_list if c})
            except (TypeError, ValueError):
                continue

        # coverage.py keys executed lines on the line a STATEMENT begins, so a
        # multi-line dict/call/comprehension contributes only its first line and
        # every mutant on a continuation line looks uncovered. Widen both the
        # covered set and the per-line test counts to the whole statement span,
        # or `covered` and `n_covering_tests` disagree -- and n_covering_tests
        # is a model feature, so a silently-zero value is worse than a missing
        # one. Real case: 26 mutants in 09_stats_summary.summarize(). ADR-005.
        src_path = Path(work_dir) / source_file
        if src_path.is_file():
            source_text = src_path.read_text(encoding="utf-8", errors="replace")
            widened = expand_to_statement_lines(covered, source_text)
            spans = _statement_spans(source_text)
            for ln in widened - covered:
                start = spans.get(ln)
                if start is not None and start in counts:
                    counts[ln] = counts[start]
            covered = widened
        return covered, counts

    raise CoverageError(
        f"{source_file} did not appear in the coverage report. "
        f"Files reported: {sorted(data.get('files', {}))[:10]}. "
        f"This usually means the tests never imported the module under test."
    )


def expand_to_statement_lines(covered: Set[int], source: str) -> Set[int]:
    """Widen a coverage set to every line spanned by each covered statement.

    coverage.py records the line a statement STARTS on. A multi-line dict,
    call, or comprehension therefore contributes one line, and mutants landing
    on its continuation lines look uncovered even though the statement ran.

    Outermost statements win: setdefault plus ast.walk means a nested statement
    never overwrites its parent's mapping, matching what coverage records.
    """
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(covered)
    span: Dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            end = getattr(node, "end_lineno", None) or node.lineno
            for ln in range(node.lineno, end + 1):
                span.setdefault(ln, node.lineno)
    widened = set(covered)
    for ln, start in span.items():
        if start in covered:
            widened.add(ln)
    return widened


def _statement_spans(source: str) -> Dict[int, int]:
    """{any line -> the line its enclosing statement starts on}."""
    import ast
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    span: Dict[int, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            end = getattr(node, "end_lineno", None) or node.lineno
            for ln in range(node.lineno, end + 1):
                span.setdefault(ln, node.lineno)
    return span
