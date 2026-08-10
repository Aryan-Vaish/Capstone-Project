"""
Mutant Line Resolution
=========================
Maps a `mutmut show` diff back to the absolute line number in the original
source file, and parses mutmut's mangled mutant ids into (class, function).

THE BUG THIS FIXES
------------------
`mutmut show` renders the *isolated function* as its own module and diffs that
(mutmut/__main__.py:1691-1714):

    orig_code, mutant_code = (cst.Module([function]).code.strip() for function in functions)
    unified_diff(orig_code.split("\\n"), mutant_code.split("\\n"), ...)

So every `@@` hunk offset is 1-based **from the function's first rendered line**,
not from the file. Taxonomy v1 returned that number verbatim as the source line.

Measured on the existing corpus: `is_eligible_for_free_shipping` is defined at
file line 10 with its body at line 11, and its two mutants were stored at lines
1 and 2. `count_vowels` (file lines 12-18) was stored at 2, 3, 5, 6. Only
functions that happen to be first in their file were ever correct.

Three things downstream consumed that wrong number:
  * the coverage gate compares it against file-absolute coverage lines, so the
    covered/uncovered split was wrong for every non-first function;
  * the AST context flags (in_conditional / in_loop / in_exception) read the
    wrong line entirely;
  * the `location` string shown to the user pointed at the wrong code.

WHY ARITHMETIC ALONE IS NOT ENOUGH
----------------------------------
`absolute = def_line + relative - 1` looks sufficient but is not, because
`read_original_function` (mutmut/__main__.py:1686-1688) prepends the function's
`leading_lines` -- comments and blank lines above the `def` -- and `.strip()`
then removes leading blanks but not leading comments. Decorators are part of the
node and are rendered too. So the rendered block's line 1 may be a comment, a
decorator, or the `def`, depending on what sits above the function.

Rather than model all of that, we locate the mutated line **by content** within
the enclosing function's span and use the relative offset only to disambiguate
when the same text appears more than once. If the text cannot be found, we
return None and count it -- never a guess, because a wrong line number is worse
than a missing one: it silently corrupts the dataset instead of showing up.
"""
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# mutmut/mutation/trampoline_templates.py:1 -- U+01C1, the class-name separator
CLASS_NAME_SEPARATOR = "ǁ"


@dataclass(frozen=True)
class FunctionRef:
    """A function as named by mutmut's mangling scheme."""
    function_name: str
    class_name: Optional[str] = None

    @property
    def qualified(self) -> str:
        return f"{self.class_name}.{self.function_name}" if self.class_name else self.function_name


def parse_mutant_id(mutant_id: str) -> Tuple[FunctionRef, Optional[int]]:
    """Split `module.x_withdraw__mutmut_3` into its function and mutant index.

    mutmut mangles module-level functions as `x_<name>` and methods as
    `x<SEP><Class><SEP><name>` (trampoline_templates.py:22-30). Taxonomy v1 only
    stripped the `__mutmut_N` suffix, so it reported the function name as
    `module.x_withdraw` -- which is why generated test stubs were named after
    the module instead of the function under test, and why any corpus module
    containing a class would have produced a name with a raw U+01C1 in it.
    """
    index: Optional[int] = None
    name = mutant_id
    if "__mutmut_" in name:
        name, _, tail = name.rpartition("__mutmut_")
        try:
            index = int(tail)
        except ValueError:
            index = None

    # strip the leading `<module>.` qualifier, if present
    if "." in name:
        name = name.rsplit(".", 1)[-1]

    if CLASS_NAME_SEPARATOR in name:
        parts = name.split(CLASS_NAME_SEPARATOR)
        # ['x', 'ClassName', 'method']
        if len(parts) >= 3:
            return FunctionRef(function_name=parts[-1], class_name=parts[-2]), index
        return FunctionRef(function_name=parts[-1]), index

    if name.startswith("x_"):
        name = name[2:]
    return FunctionRef(function_name=name), index


@dataclass(frozen=True)
class FunctionSpan:
    """Where a function lives in the original file, 1-based and inclusive."""
    ref: FunctionRef
    start: int          # the `def` line (or first decorator)
    end: int
    body_start: int     # first line of the body


def build_function_index(source: str) -> Dict[str, FunctionSpan]:
    """Map both `Class.method` and bare `method` to their span in `source`.

    Bare names are registered too so lookup still works when the mutant id gives
    us no class context, but a bare name that is ambiguous across classes is
    deliberately dropped rather than resolved arbitrarily.
    """
    tree = ast.parse(source)
    spans: Dict[str, FunctionSpan] = {}
    ambiguous: set = set()

    def visit(node: ast.AST, class_name: Optional[str] = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                visit(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = child.lineno
                if child.decorator_list:
                    start = min(d.lineno for d in child.decorator_list)
                end = getattr(child, "end_lineno", start) or start
                body_start = child.body[0].lineno if child.body else start
                ref = FunctionRef(function_name=child.name, class_name=class_name)
                span = FunctionSpan(ref=ref, start=start, end=end, body_start=body_start)

                spans[ref.qualified] = span
                bare = child.name
                if bare in spans and spans[bare].ref.qualified != ref.qualified:
                    ambiguous.add(bare)
                else:
                    spans[bare] = span

                # nested functions are mutated too
                visit(child, class_name)

    visit(tree)
    for name in ambiguous:
        spans.pop(name, None)
    return spans


def resolve_line(
    source_lines: List[str],
    span: Optional[FunctionSpan],
    removed_line: Optional[str],
    relative_line: Optional[int],
) -> Optional[int]:
    """Absolute 1-based line of the mutated statement, or None if unresolvable.

    Strategy, in order:
      1. find every line in the function's span whose stripped text matches the
         diff's removed line exactly;
      2. if exactly one, that is the answer;
      3. if several, pick the one closest to the offset the hunk implies;
      4. otherwise return None.

    There is deliberately no "trust the arithmetic" fallback. libcst preserves
    source formatting verbatim, so a removed line that cannot be found in its
    own function's span means something is genuinely wrong -- a stale source
    file, the wrong function, or a construct we are mis-parsing. Guessing a
    plausible-looking line there would put a wrong number in the dataset that is
    indistinguishable from a right one, which is precisely the failure mode this
    module exists to eliminate. Unresolved mutants are counted and reported
    instead.
    """
    if not removed_line:
        return None
    target = removed_line.strip()
    if not target:
        return None

    if span is None:
        # No function context: match against the whole file, but only accept an
        # unambiguous hit -- guessing here is how v1 produced silent corruption.
        matches = [i + 1 for i, l in enumerate(source_lines) if l.strip() == target]
        return matches[0] if len(matches) == 1 else None

    lo, hi = span.start, min(span.end, len(source_lines))
    matches = [i for i in range(lo, hi + 1) if source_lines[i - 1].strip() == target]

    if len(matches) == 1:
        return matches[0]

    if matches:
        if relative_line is None:
            return matches[0]
        # The rendered block starts at or just above `span.start`; the implied
        # absolute line is only approximate, so use it to choose among real
        # textual matches rather than as the answer itself.
        implied = span.start + relative_line - 1
        return min(matches, key=lambda m: abs(m - implied))

    return None


def parse_hunk_relative_line(diff_text: str) -> Optional[int]:
    """First removed line's offset within the diff's hunk, 1-based.

    Note the hunk header may be `@@ -3,4 +3,4 @@` *or* `@@ -3 +3 @@` for a
    single-line hunk -- taxonomy v1's regex required the comma and so silently
    failed on the second form.
    """
    import re

    lines = diff_text.splitlines()
    hunk_start: Optional[int] = None
    offset = 0
    for line in lines:
        m = re.match(r"@@ -(\d+)(?:,(\d+))? \+", line)
        if m:
            hunk_start = int(m.group(1))
            offset = 0
            continue
        if hunk_start is None:
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            return hunk_start + offset
        if line.startswith("+"):
            continue          # added lines consume no original-file line
        offset += 1           # context line
    return hunk_start


def resolve_mutant_line(
    source_path: Path,
    mutant_id: str,
    diff_text: str,
    removed_line: Optional[str],
    function_index: Optional[Dict[str, FunctionSpan]] = None,
    source_lines: Optional[List[str]] = None,
) -> Tuple[Optional[int], FunctionRef, str]:
    """Convenience wrapper: returns (absolute_line, function_ref, resolution_method).

    `resolution_method` is recorded per mutant so a run can report how many
    lines were resolved by content match vs. fell back to arithmetic vs. failed.
    """
    ref, _ = parse_mutant_id(mutant_id)

    if source_lines is None:
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
    if function_index is None:
        function_index = build_function_index("\n".join(source_lines))

    span = function_index.get(ref.qualified) or function_index.get(ref.function_name)
    relative = parse_hunk_relative_line(diff_text)

    if not removed_line:
        return None, ref, "no_removed_line"

    target = removed_line.strip()
    if span is not None:
        lo, hi = span.start, min(span.end, len(source_lines))
        exact = [i for i in range(lo, hi + 1) if source_lines[i - 1].strip() == target]
        if len(exact) == 1:
            return exact[0], ref, "content_unique"
        if len(exact) > 1:
            return resolve_line(source_lines, span, removed_line, relative), ref, "content_disambiguated"

    resolved = resolve_line(source_lines, span, removed_line, relative)
    if resolved is None:
        # Distinguish "we could not find the function" from "we found it but the
        # line is not in it" -- the first points at the mutant-id parser, the
        # second at the diff. Both are reported, never silently filled in.
        return None, ref, "unresolved_no_function" if span is None else "unresolved"
    return resolved, ref, "file_wide_match"
