# mutmut 3.7.0 — Authoritative Mutation Operator Catalogue

**Source:** `mutmut/mutation/mutators.py`, from the official `mutmut-3.7.0-py3-none-any.whl`
(sha256 of wheel recorded in `reports/`). Transcribed 2026-08-11.

**Why this document exists.** Taxonomy v1 was built by pattern-matching observed diffs, which left
73.5% of mutants (114/155) classified `unknown` and included three categories mutmut can never
produce. This catalogue is read directly from mutmut's source, so taxonomy v2 can be stated as a
*total function over mutmut 3.7.0's mutation table* rather than a set of heuristics that happened to
fit the sample.

---

## 1. Dispatch table

`mutation_operators` (`mutators.py:252-268`) maps a libcst node type to a generator function. Every
mutant mutmut 3.7.0 can produce comes from exactly one of these fifteen entries:

| # | Node type | Operator function |
|---|---|---|
| 1 | `cst.BaseNumber` | `operator_number` |
| 2 | `cst.BaseString` | `operator_string` |
| 3 | `cst.Name` | `operator_name` |
| 4 | `cst.Assign` | `operator_assignment` |
| 5 | `cst.AnnAssign` | `operator_assignment` |
| 6 | `cst.AugAssign` | `operator_augmented_assignment` |
| 7 | `cst.UnaryOperation` | `operator_remove_unary_ops` |
| 8 | `cst.Call` | `operator_dict_arguments` |
| 9 | `cst.Call` | `operator_arg_removal` |
| 10 | `cst.Call` | `operator_symmetric_string_methods_swap` |
| 11 | `cst.Call` | `operator_unsymmetrical_string_methods_swap` |
| 12 | `cst.Lambda` | `operator_lambda` |
| 13 | `cst.CSTNode` | `operator_keywords` |
| 14 | `cst.CSTNode` | `operator_swap_op` |
| 15 | `cst.Match` | `operator_match` |

---

## 2. Operator semantics

### 2.1 `operator_swap_op` — the binary/comparison/logical operator table

`_operator_mapping` (`mutators.py:172-207`). Applies to `BinaryOperation`, `UnaryOperation`,
`BooleanOperation`, `ComparisonTarget`, and `AugAssign`. Transcribed verbatim:

| From | To | Family |
|---|---|---|
| `Plus` (unary `+`) | `Minus` | arithmetic |
| `Minus` (unary `-`) | `Plus` | arithmetic |
| `Add` (`+`) | `Subtract` | arithmetic |
| `Subtract` (`-`) | `Add` | arithmetic |
| `Multiply` (`*`) | `Divide` | arithmetic |
| `Divide` (`/`) | `Multiply` | arithmetic |
| `FloorDivide` (`//`) | `Divide` | arithmetic |
| `Modulo` (`%`) | `Divide` | arithmetic |
| `Power` (`**`) | `Multiply` | arithmetic |
| `LeftShift` (`<<`) | `RightShift` | bitwise |
| `RightShift` (`>>`) | `LeftShift` | bitwise |
| `BitAnd` (`&`) | `BitOr` | bitwise |
| `BitOr` (`\|`) | `BitAnd` | bitwise |
| `BitXor` (`^`) | `BitAnd` | bitwise |
| `LessThan` (`<`) | `LessThanEqual` | comparison |
| `LessThanEqual` (`<=`) | `LessThan` | comparison |
| `GreaterThan` (`>`) | `GreaterThanEqual` | comparison |
| `GreaterThanEqual` (`>=`) | `GreaterThan` | comparison |
| `Equal` (`==`) | `NotEqual` | equality |
| `NotEqual` (`!=`) | `Equal` | equality |
| `And` (`and`) | `Or` | logical |
| `Or` (`or`) | `And` | logical |

Plus the augmented-assignment forms of every arithmetic/bitwise entry: `AddAssign`→`SubtractAssign`,
`SubtractAssign`→`AddAssign`, `MultiplyAssign`→`DivideAssign`, `DivideAssign`→`MultiplyAssign`,
`FloorDivideAssign`→`DivideAssign`, `ModuloAssign`→`DivideAssign`, `LeftShiftAssign`→`RightShiftAssign`,
`RightShiftAssign`→`LeftShiftAssign`, `BitAndAssign`→`BitOrAssign`, `BitOrAssign`→`BitAndAssign`,
`BitXorAssign`→`BitAndAssign`, `PowerAssign`→`MultiplyAssign`.

**Note:** comparison mutations are *boundary-only* (`<`↔`<=`). mutmut never swaps `<` for `>`.

### 2.2 `operator_number` (`mutators.py:24-30`)

`Integer`/`Float` → `repr(value + 1)`; `Imaginary` → `value + 1j`. Every numeric literal yields
exactly one mutant. **Taxonomy v1 had no rule for this** — a major `unknown` contributor.

### 2.3 `operator_string` (`mutators.py:33-58`)

For `SimpleString` only. Triple-quoted strings return early (`mutators.py:40-43`) — **docstrings are
never mutated**, which is worth stating in the report since the Tier-0 triage filter also targets
docstrings. Three mutations per string literal:

1. `"foo"` → `"XXfooXX"` (wrap)
2. lowercase every non-escape run
3. uppercase every non-escape run

Mutations producing an unchanged value are skipped. The `XX` wrapper is mutmut's signature and
explains most of the 35 `equivalent_suspected` rows in dataset v1.

### 2.4 `operator_name` (`mutators.py:161-169`)

`True`→`False`, `False`→`True`, `deepcopy`→`copy`. **v1 had no rule** — another `unknown` source.

### 2.5 `operator_assignment` (`mutators.py:229-241`)

`a = b` → `a = None`; `a = None` → `a = ""`. Annotated assignments without a value are skipped.

This is the real source of the `None`→`""` behaviour that taxonomy v1's `null_check_removal` rule
tried to catch — and missed, because the rule searched for `''` while mutmut emits `""`.

### 2.6 `operator_augmented_assignment` (`mutators.py:224-226`)

`x += y` → `x = y`. Distinct from the `AddAssign`→`SubtractAssign` swap in 2.1; both fire.

### 2.7 `operator_remove_unary_ops` (`mutators.py:142-144`)

Removes `not` and `~`, yielding the bare operand.

### 2.8 `operator_keywords` (`mutators.py:147-158`)

`Is`→`IsNot`, `IsNot`→`Is`, `In`→`NotIn`, `NotIn`→`In`, `Break`→`Return`, `Continue`→`Break`.

Note the asymmetry: `Break`→`Return` but `Continue`→`Break`.

### 2.9 `operator_arg_removal` (`mutators.py:86-97`)

Two behaviours on every `Call`:
- replace each non-starred, non-`None` argument with `None`
- if the call has more than one argument, drop each argument in turn

High-volume on any code that calls functions. **v1 had no rule** — the single largest `unknown`
contributor in a typical module.

### 2.10 `operator_dict_arguments` (`mutators.py:68-83`)

`dict(a=b)` → `dict(aXX=b)`. Only for calls to the literal name `dict`, and only when every argument
is a keyword argument.

### 2.11 String-method swaps (`mutators.py:100-139`)

Symmetric pairs, both directions: `lower`/`upper`, `lstrip`/`rstrip`, `find`/`rfind`, `ljust`/`rjust`,
`index`/`rindex`, `removeprefix`/`removesuffix`, `partition`/`rpartition`.

Asymmetric: `split`/`rsplit`, fired only when the call has two positional args or a `maxsplit` keyword.

### 2.12 `operator_lambda` (`mutators.py:61-65`)

`lambda …: <body>` → `lambda …: None`; if the body is already `None`, → `0`.

### 2.13 `operator_match` (`mutators.py:244-248`)

Drops each `case` in turn, only when a `match` has more than one case.

---

## 3. Operators mutmut 3.7.0 does NOT implement

Taxonomy v1 declared these; nothing in the dispatch table can produce them. They were dead one-hot
columns contributing pure noise to the model:

- **`statement_deletion`** — no operator removes statements. (`operator_match` removes `case` blocks
  and `operator_arg_removal` removes call arguments; neither is statement deletion.)
- **`return_value_replacement`** — no operator rewrites `return` expressions. `Break`→`Return` inserts
  a `return`, but that is loop-control, not return-value replacement.

Both belong to other tools (mutpy, cosmic-ray). Removing them is a correctness fix, not a
simplification.

Also absent, despite the `# TODO` at `mutators.py:282`: **decorator removal**. Taxonomy v2 must not
declare a `decorator_removal` category.

---

## 4. Diff format — why line numbers were wrong

`get_diff_for_mutant` (`__main__.py:1691-1714`):

```python
orig_code, mutant_code = (cst.Module([function]).code.strip() for function in functions)
unified_diff(orig_code.split("\n"), mutant_code.split("\n"), ...)
```

The **isolated function** is rendered as its own module and diffed. Consequences:

1. `@@` hunk offsets are **1-based from the function's first rendered line**, not from the file.
2. `read_original_function` (`__main__.py:1686-1688`) prepends the function's `leading_lines`
   (comments and blank lines above the `def`), so line 1 may be a comment, not the `def`.
3. `.strip()` removes leading blank lines but not leading comments.
4. Decorators are part of the `FunctionDef` node and are included.

So no fixed arithmetic maps relative → absolute reliably. Taxonomy v2's line resolver locates the
mutated line **by content** within the enclosing function's span, using the relative offset only to
disambiguate duplicate matches.

## 5. Mutant naming

`mangle_function_name` (`trampoline_templates.py:22-30`):

- module-level function `foo` → `x_foo`
- method `bar` of class `Baz` → `xǁBazǁbar`, where `ǁ` is U+01C1 (`CLASS_NAME_SEPARATOR`)

Full mutant id as emitted by `mutmut results`: `<module>.<mangled>__mutmut_<N>`, e.g.
`module.x_withdraw__mutmut_3` or `rate_limiter.xǁRateLimiterǁallow__mutmut_1`.

Taxonomy v1's parser (`generator.py:133`) only stripped `__mutmut_N`, leaving `module.x_withdraw` as
the "function name" — which is why generated test stubs were named after the module rather than the
function under test. Any corpus module containing a class would additionally break on the `ǁ`
separator.
