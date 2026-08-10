# Dataset v1 — Post-Mortem

**Status:** archived and superseded by schema v2. Not used for any reported result.
**Archived to:** `services/mutation-testing/storage/archive/mutants_v1_2026-08-11.db`
**Analysed:** 2026-08-11, immediately before the v2 migration.

This document exists so the v1 numbers can be cited honestly as a "before" column
rather than quietly disappearing. Every figure below was measured directly from the
committed `mutants.db`, not recalled from the Days 1–4 summary.

---

## Headline

| Metric | v1 | Why it mattered |
|---|---|---|
| Rows stored | 155 | — |
| **Distinct mutants** | **58** | Rows were duplicated up to 8× |
| Distinct `run_id`s | 13 | Each one a `pytest` invocation writing to the production DB |
| Operator `unknown` | **114 / 155 (73.5%)** | The one-hot operator feature was mostly noise |
| Distinct module identities | 1 (`module.py`) | Module-aware evaluation was impossible |
| Distinct `(complexity, ast_depth)` pairs | **4** | These features *were* a module fingerprint |

The reported v1 model figures — 85.7% train / 73.3% validation accuracy — are not
reproducible and should not be cited. The reasons are structural, and are listed below.

---

## 1. Test runs wrote into the training set

`storage/db.py` resolved its path at import time, so there was no way to redirect it.
Three tests in `test_main.py` each POST `/mutation/run`, and `test_ml_pipeline.py`
inserted a synthetic row directly. Every `pytest` invocation therefore appended to the
real dataset.

Duplication profile:

| Copies of the same mutant | Number of mutants |
|---|---|
| 8× | 6 |
| 3× | 27 |
| 2× | 1 |
| 1× | 24 |

A stray row with `mutant_id = 'test.mutant.1'` and `module = 'test_module.py'` came
from the storage unit test.

**Consequence for the reported accuracy.** `train.py` used
`train_test_split(X, y, test_size=0.25, random_state=42)`, which shuffles *rows*. With
34 of 58 mutants present more than once, byte-identical rows landed on both sides of
the split. The 73.3% "validation accuracy" was therefore measured partly on rows the
model had memorised verbatim.

**Fixed by:** `MUTATION_DB_PATH` resolved per call (`storage/db.py`), a session-scoped
autouse fixture in `tests/conftest.py`, and a `UNIQUE(dataset_schema_version,
module_key, suite, mutant_id)` constraint with `INSERT OR REPLACE`.

---

## 2. 73.5% of mutants were unclassified

| Operator | Rows | Share |
|---|---|---|
| `unknown` | 114 | 73.5% |
| `conditional_boundary` | 22 | 14.2% |
| `arithmetic_replacement` | 18 | 11.6% |
| `negate_conditional` | 1 | 0.6% |
| `statement_deletion` | 0 | — |
| `return_value_replacement` | 0 | — |
| `null_check_removal` | 0 | — |
| `constant_replacement` | 0 | — |

Four of the eight declared categories had zero rows, and three of those were
*structurally unreachable*:

- **`statement_deletion`** and **`return_value_replacement`** — mutmut 3.7.0 implements
  neither. They belong to mutpy/cosmic-ray. Verified against mutmut's own dispatch
  table (`mutmut/mutation/mutators.py:252-268`); see
  [`mutmut_3.7_operator_catalogue.md`](mutmut_3.7_operator_catalogue.md) §3.
- **`null_check_removal`** — its rule searched the added text for `''`, but mutmut's
  `operator_assignment` emits `""` (double quotes, `mutators.py:237`). The pattern could
  never match.

The classifier itself was also structurally wrong: it joined *all* removed lines into
one string and *all* added lines into another, then ran ordered regexes over the pair.
A `-` anywhere in the removed block matched the arithmetic rule regardless of what had
actually changed.

The dominant real mutation types in the sample — call-argument replacement/removal,
numeric-literal increment, string wrapping, assignment-to-`None`, `True`/`False` flips
— had no rule at all.

**Fixed by:** taxonomy v2 (23 operators, 8 families) transcribed from mutmut's source,
plus a token-level classifier (`src/mutators/classifier.py`) that aligns the two token
streams with `difflib` and keys on the minimal substitution.

---

## 3. No module identity

`main.py` stored `source_path.name`. All three corpus modules were named `module.py`:

| `module` | Rows |
|---|---|
| `module.py` | 153 |
| `test_module.py` | 2 |

So there was no way to group by module, which made three things impossible: a leak-free
train/test split, a held-out benchmark, and any per-module result table.

**Fixed by:** `module_key` (service-relative path, e.g.
`corpus/02_discount_pricing/discount_pricing.py`) plus `module_slug`, and a corpus
naming convention that gives every module a unique importable filename.

---

## 4. Structural features were a module fingerprint

`complexity` and `ast_depth` were computed over the **entire module**, so every mutant
in a file carried identical values. Across 155 rows there were exactly four distinct
pairs — one per source file:

| `complexity` | `ast_depth` | Rows | Corresponds to |
|---|---|---|---|
| 4 | 6 | 81 | `module3/module.py` |
| 2 | 5 | 48 | `example/module.py` |
| 9 | 8 | 24 | `module2/module.py` |
| 1 | 1 | 2 | the synthetic test row |

Under a random split this hands the model a module ID it can use to memorise per-module
label distributions. Under a correct grouped split it carries no within-module signal at
all. Either way it is not a useful feature.

**Fixed by:** function-scoped features (`func_complexity`, `func_loc`, `func_num_params`,
`func_num_returns`, `func_num_branches`, `node_depth_at_line`, `rel_position_in_func`),
which is only possible now that line numbers are real file coordinates.

---

## 5. Line numbers were function-relative

The single most damaging defect, and the reason the v1 rows are *wrong* rather than
merely *stale*.

`mutmut show` renders the isolated function as its own module and diffs that
(`mutmut/__main__.py:1691-1714`), so hunk offsets are 1-based from the function's first
line. v1 stored that offset verbatim as the source line.

Measured examples:

| Function | Real file lines | v1 stored |
|---|---|---|
| `is_eligible_for_free_shipping` (`module2`) | 10–11 | **1, 2** |
| `count_vowels` (`module3`) | 12–18 | **2, 3, 5, 6** |
| `withdraw` (`example`) | 1–4 | 2, 3, 4 ✓ (correct by coincidence) |
| `apply_discount` (`module2`) | 1–7 | 2–7 ✓ (correct by coincidence) |

Only functions that happened to be first in their file were ever right.

Three consumers used the wrong number:

1. **The coverage gate** — `covered = m.line_number in covered_lines` compared a
   function-relative offset against file-absolute coverage data. Every covered/uncovered
   decision for a non-first function was arbitrary. The 13 `uncovered` rows are not
   trustworthy.
2. **The AST context flags** — `in_conditional` / `in_loop` / `in_exception` were read at
   the wrong line.
3. **The user-facing `location`** — pointed at the wrong code.

**Fixed by:** `src/mutators/line_resolver.py`, which locates the mutated line by content
within the enclosing function's span and uses the hunk offset only to disambiguate
duplicate matches. It returns `None` rather than guessing, and the count of unresolved
mutants is reported per run.

---

## 6. Triage evidence was discarded

`arid_filter.triage_mutant` returned a status, a confidence and a reason code; the call
site read only `.status`. Neither the confidence nor the reason reached the database or
the Evidence Object — despite the module docstring stating the decision "stays auditable
— Governance needs the count".

Two false-positive sources were also present:

- Patterns were matched against the **whole diff including context lines**, so one
  `logging.info(...)` anywhere nearby flagged every mutant in that function.
- A `docstring` pattern existed, but mutmut never mutates docstrings —
  `operator_string` returns early for triple-quoted strings (`mutators.py:40-43`). It
  could only ever fire on a context line.

So the 35 `equivalent_suspected` rows are an overcount of unknown size.

**Fixed by:** persisting `triage_status`, `triage_confidence` and `triage_reason_code`;
matching only changed lines; dropping the docstring pattern; and identifying cosmetic
methods (`__repr__`, `__str__`) from the parsed function name instead of by grepping.

---

## Decision: purge rather than migrate

No v1 row survives the v2 fixes intact. Line numbers are wrong for most functions,
operators are 73.5% unlabelled under a taxonomy with unreachable categories, module
identity does not exist, the structural features are module IDs, the coverage flags
derive from the bad line numbers, and 62% of rows are duplicates.

Migration would mean recomputing every field from the diff — which is exactly what
re-collection does, in a few minutes of mutmut time, with correct provenance attached.

The database is archived (not deleted) and remains in git history. These numbers are
reported in the write-up as the "before" column of the data-quality table, and this
document is the methodology-transparency record behind them.
