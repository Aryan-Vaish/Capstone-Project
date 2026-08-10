# ADR-003: Purge dataset v1, and version the dataset schema

**Status:** accepted · **Date:** 2026-08-11

## Context

Dataset v1 was 155 rows accumulated over Days 1–4. Measured directly before migration
(full analysis in the [post-mortem](../Research/experiments/dataset_v1_postmortem.md)):

| Finding | Measurement |
|---|---|
| Rows were duplicates | 155 rows = **58 distinct mutants**; one present 8× |
| Test runs wrote to the production DB | **13 distinct `run_id`s** — one per `pytest` invocation |
| Operators unlabelled | **73.5%** `unknown` (ADR-002) |
| Module identity | **one value**, `module.py`, for 153 of 155 rows |
| Structural features | **4 distinct `(complexity, ast_depth)` pairs** — one per source file |
| Line numbers | function-relative, so wrong for every non-first function |

Two of these are not merely quality problems but invalidate the reported model result.
`train_test_split` shuffles rows, so with 34 of 58 mutants duplicated, byte-identical rows
landed on both sides of the split — the reported 73.3% "validation accuracy" was measured
partly on memorised training rows. And with `complexity`/`ast_depth` taking exactly four
values, those features *were* a module identifier the model could use to memorise
per-module label distributions.

The line-number defect is the most damaging. `mutmut show` renders the isolated function
as its own module and diffs that (`mutmut/__main__.py:1691-1714`), so hunk offsets are
1-based from the function's first line. v1 stored that offset as the source line.
`is_eligible_for_free_shipping` lives at file lines 10–11 and had its mutants recorded at
lines **1 and 2**. Three consumers used that number: the coverage gate (comparing it
against file-absolute coverage data), the AST context flags, and the user-facing
`location`.

## Decision

### 1. Purge v1 rather than migrate it — but archive first

No v1 row survives the v2 fixes intact: line numbers are wrong, operators are 73.5%
unlabelled under a taxonomy with unreachable categories, module identity does not exist,
structural features are module IDs, the coverage flags derive from the bad line numbers,
and 62% of rows are duplicates. Migration would mean recomputing every field from the
diff — which is exactly what re-collection does, in minutes, with correct provenance.

The database is copied to `storage/archive/mutants_v1_2026-08-11.db` and remains in git
history. Its statistics are written up as the "before" column of the data-quality table.

### 2. Version the dataset schema

Every row carries `dataset_schema_version`, and `train.py` filters on it. Roughly ten
lines that permanently eliminate the class of bug where a taxonomy or feature change
silently poisons a training set by mixing incompatible vectors.

Rows also carry `taxonomy_version`, `pipeline_git_sha` and `corpus_git_sha`.

### 3. Make identity real and writes idempotent

`module_key` is the service-relative path (`corpus/02_discount_pricing/discount_pricing.py`)
plus a `module_slug` for grouping, and the corpus convention requires unique importable
filenames. `UNIQUE(dataset_schema_version, module_key, suite, mutant_id)` with
`INSERT OR REPLACE` makes re-collection correct rows in place instead of appending.

### 4. Resolve the DB path at call time

`db_path()` reads `MUTATION_DB_PATH` per call instead of freezing a constant at import.
This is what lets `tests/conftest.py` point the whole session at a temp file — the
mechanism that makes the pollution in finding #2 impossible to repeat.

### 5. Persist the triage decision

`triage_status`, `triage_confidence` and `triage_reason_code` are columns. v1 computed all
three and kept only the status, despite `arid_filter`'s docstring promising the decision
"stays auditable — Governance needs the count". A per-run report additionally lists every
filtered mutant with its reason.

## Alternatives considered

**Migrate v1 by recomputing fields from stored diffs.** Impossible: v1 never stored the
diff. Only the derived (and wrong) fields were kept.

**Keep v1 rows under a `v1` version tag and train on the union.** Rejected. The rows are
wrong, not merely old, and their presence in the same table invites exactly the accident
the versioning exists to prevent.

**Deduplicate v1 in place and keep it.** Would fix one of six problems.

## Consequences

**Good.** The next collection produces a dataset that supports a leak-free grouped split
and a genuine held-out benchmark — neither of which was possible before. Test runs can no
longer touch the real dataset. Re-collection is idempotent, so a partial run can simply
be repeated.

**Costs.** All previously reported model numbers are withdrawn. Expect the honest,
module-grouped figures to come in **below** v1's 73.3%: that is the correct result, and
it should be reported next to the post-mortem explaining why 73.3% was not real. A
defensible 0.65 is worth more than an indefensible 0.73.

**Risk accepted.** Re-collection requires WSL, which is not installed on every team
machine. Until it runs, the pipeline has no model and degrades to unranked-but-explained
output — a supported state, not an error.

## Verification

- `tests/test_ml_pipeline.py::test_reinsert_updates_instead_of_duplicating`
- `tests/test_ml_pipeline.py::test_training_population_excludes_uncovered_and_arid`
- `tests/test_line_resolver.py` — asserts the real file coordinates (lines 10–11, not 1–2)
- `tests/conftest.py` — session-scoped `MUTATION_DB_PATH` redirection
- `scripts/collect_dataset.py` — gates on unclassified rate, unresolved-line rate, and
  measured-score-vs-declared-tier before any training is allowed
