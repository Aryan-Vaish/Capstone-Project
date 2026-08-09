"""
Feature Extractor
====================
Turns a Mutant record + its source file into a flat feature dict, for
persistence and later ML training. Deliberately uses only Python's
built-in `ast` module for complexity/context features (NOT the `radon`
package) -- one fewer third-party dependency to fight wheel-availability
battles over, given how much of Days 1-2 was spent on exactly that
problem. `ast` gives us everything we actually need here.
"""
import ast
from pathlib import Path
from typing import Dict, Optional


def _cyclomatic_complexity(tree: ast.AST) -> int:
    """Simple McCabe-style count: 1 + number of decision points in the module."""
    complexity = 1
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.For, ast.While, ast.ExceptHandler)):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1
    return complexity


def _context_flags_at_line(tree: ast.AST, line_number: int) -> Dict[str, bool]:
    """Walks the AST to determine if the given line sits inside a
    conditional, loop, or exception handler."""
    flags = {"in_conditional": False, "in_loop": False, "in_exception": False}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def _visit_block(self, node, kind):
            self.stack.append(kind)
            start = node.lineno
            end = max(
                (getattr(n, "lineno", start) for n in ast.walk(node)), default=start
            )
            if start <= line_number <= end:
                flags[kind] = True
            self.generic_visit(node)
            self.stack.pop()

        def visit_If(self, node):
            self._visit_block(node, "in_conditional")

        def visit_For(self, node):
            self._visit_block(node, "in_loop")

        def visit_While(self, node):
            self._visit_block(node, "in_loop")

        def visit_ExceptHandler(self, node):
            self._visit_block(node, "in_exception")

    Visitor().visit(tree)
    return flags


def extract_features(source_file: Path, line_number: Optional[int], operator: str,
                       covered: bool) -> Dict:
    """
    Returns a flat feature dict for one mutant. `covered` is whether the
    mutant's line was in the coverage set (from Day 2's coverage/tracker.py) --
    included as a feature too, not just used for the uncovered/active split,
    since "barely covered" vs "heavily covered" code may behave differently
    even among covered survivors.
    """
    source_code = source_file.read_text()
    tree = ast.parse(source_code)

    complexity = _cyclomatic_complexity(tree)
    ast_depth = max((_node_depth(tree, n) for n in ast.walk(tree)), default=0)

    context = {"in_conditional": False, "in_loop": False, "in_exception": False}
    if line_number is not None:
        context = _context_flags_at_line(tree, line_number)

    return {
        "operator": operator,
        "line_number": line_number or -1,
        "complexity": complexity,
        "ast_depth": ast_depth,
        "in_conditional": int(context["in_conditional"]),
        "in_loop": int(context["in_loop"]),
        "in_exception": int(context["in_exception"]),
        "covered": int(covered),
    }


def _node_depth(root: ast.AST, target: ast.AST) -> int:
    """Depth of `target` within `root`'s tree, 0 if root itself."""
    if root is target:
        return 0
    for child in ast.iter_child_nodes(root):
        d = _node_depth(child, target)
        if d >= 0:
            return d + 1
    return -1
