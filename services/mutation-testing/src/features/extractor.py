"""
Feature Extractor
====================
Turns a Mutant + its source file into a flat feature dict, for persistence and
later ML training. Uses only Python's built-in `ast` module for the structural
features rather than `radon` -- one fewer third-party wheel to fight over,
given how much of Days 1-2 went to exactly that class of problem.

WHY THESE FEATURES CHANGED (v2)
-------------------------------
v1 computed `complexity` and `ast_depth` over the WHOLE MODULE. Every mutant in
a file therefore carried identical values, which makes them useless at best and
harmful at worst:

  * under a module-aware (leak-free) train/test split they carry no
    within-module signal at all -- pure noise;
  * under a random split they act as a module fingerprint, letting the model
    identify which file a row came from and memorise its label distribution.

Dataset v1 had exactly four distinct (complexity, ast_depth) pairs across 155
rows -- one per source file. They *were* a module ID.

v2 scopes every structural feature to the enclosing function, which is possible
now that line numbers are real file coordinates (see mutators/line_resolver.py).
`line_number` itself is stored for traceability but deliberately kept OUT of the
model vector, for the same fingerprinting reason.
"""
import ast
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Canonical ordering of the numeric part of the model input vector. Anything
# that builds or consumes a feature vector imports this rather than hardcoding
# an order, so a feature added here cannot silently misalign the model.
NUMERIC_FEATURES: List[str] = [
    "func_complexity",
    "func_loc",
    "func_num_params",
    "func_num_returns",
    "func_num_branches",
    "node_depth_at_line",
    "rel_position_in_func",
    "in_conditional",
    "in_loop",
    "in_exception",
    "covered",
    # How many DISTINCT tests execute this line, via coverage's dynamic
    # contexts. This is the closest thing to a causal predictor of "will a
    # mutation here be caught" that can be obtained cheaply: a line touched by
    # one incidental test is far likelier to harbour a surviving mutant than one
    # six tests assert against. It is also what the earlier plan's "distance to
    # nearest assertion" idea was really reaching for.
    "n_covering_tests",
]

# Stored for traceability and grouping, never fed to the model.
NON_MODEL_FEATURES: List[str] = ["operator", "operator_family", "line_number"]


def _decision_points(node: ast.AST) -> int:
    """McCabe-style count of decision points within `node` (exclusive of nested
    function definitions, which get their own score)."""
    count = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        elif isinstance(child, (ast.IfExp, ast.comprehension)):
            count += 1
    return count


def _enclosing_function(tree: ast.AST, line_number: int) -> Optional[ast.AST]:
    """Innermost FunctionDef containing `line_number`, or None."""
    best = None
    best_span = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.lineno
        if node.decorator_list:
            start = min(d.lineno for d in node.decorator_list)
        end = getattr(node, "end_lineno", None) or start
        if start <= line_number <= end:
            span = end - start
            if best_span is None or span < best_span:
                best, best_span = node, span
    return best


def _depth_map(tree: ast.AST) -> Dict[int, int]:
    """Deepest AST nesting level reached on each source line.

    v1 computed this with `max(_node_depth(tree, n) for n in ast.walk(tree))`,
    where `_node_depth` re-walked from the root for every node -- O(n^2) over
    the AST, and file-global so every mutant got the same number. This is a
    single pass that keeps per-line resolution.
    """
    depths: Dict[int, int] = {}

    def walk(node: ast.AST, depth: int) -> None:
        line = getattr(node, "lineno", None)
        if line is not None and depth > depths.get(line, -1):
            depths[line] = depth
        for child in ast.iter_child_nodes(node):
            walk(child, depth + 1)

    walk(tree, 0)
    return depths


def _context_flags(func: ast.AST, line_number: int) -> Dict[str, bool]:
    """Whether `line_number` sits inside a conditional / loop / except block."""
    flags = {"in_conditional": False, "in_loop": False, "in_exception": False}

    def block_contains(node: ast.AST) -> bool:
        start = node.lineno
        end = getattr(node, "end_lineno", None) or start
        return start <= line_number <= end

    for node in ast.walk(func):
        if isinstance(node, ast.If) and block_contains(node):
            flags["in_conditional"] = True
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)) and block_contains(node):
            flags["in_loop"] = True
        elif isinstance(node, ast.ExceptHandler) and block_contains(node):
            flags["in_exception"] = True
    return flags


def _count_params(func: ast.AST) -> int:
    a = func.args
    return (len(a.posonlyargs) + len(a.args) + len(a.kwonlyargs)
            + (1 if a.vararg else 0) + (1 if a.kwarg else 0))


def extract_features(
    source_file: Path,
    line_number: Optional[int],
    operator: str,
    covered: bool,
    n_covering_tests: int = 0,
    source_text: Optional[str] = None,
) -> Dict:
    """Flat feature dict for one mutant.

    `covered` is whether the mutant's line was in the coverage set. It stays a
    feature as well as a routing decision, because among covered mutants the
    *degree* of coverage still matters -- which is what `n_covering_tests` is
    for when the coverage run can supply it.
    """
    from mutators.taxonomy import family_of

    if source_text is None:
        source_text = source_file.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    func = _enclosing_function(tree, line_number) if line_number is not None else None

    if func is not None:
        start = func.lineno
        if func.decorator_list:
            start = min(d.lineno for d in func.decorator_list)
        end = getattr(func, "end_lineno", None) or start
        func_loc = max(1, end - start + 1)
        func_complexity = 1 + _decision_points(func)
        func_num_params = _count_params(func)
        func_num_returns = sum(1 for n in ast.walk(func) if isinstance(n, ast.Return))
        func_num_branches = _decision_points(func)
        rel_position = round((line_number - start) / func_loc, 4)
        flags = _context_flags(func, line_number)
    else:
        # No enclosing function: module-level code, or an unresolved line.
        func_loc = 0
        func_complexity = 0
        func_num_params = 0
        func_num_returns = 0
        func_num_branches = 0
        rel_position = 0.0
        flags = {"in_conditional": False, "in_loop": False, "in_exception": False}

    depth_at_line = _depth_map(tree).get(line_number, 0) if line_number is not None else 0

    return {
        "operator": operator,
        "operator_family": family_of(operator),
        "line_number": line_number if line_number is not None else -1,
        "func_complexity": func_complexity,
        "func_loc": func_loc,
        "func_num_params": func_num_params,
        "func_num_returns": func_num_returns,
        "func_num_branches": func_num_branches,
        "node_depth_at_line": depth_at_line,
        "rel_position_in_func": rel_position,
        "in_conditional": int(flags["in_conditional"]),
        "in_loop": int(flags["in_loop"]),
        "in_exception": int(flags["in_exception"]),
        "covered": int(covered),
        "n_covering_tests": int(n_covering_tests),
    }
