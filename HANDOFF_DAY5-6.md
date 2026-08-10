# Handoff — Day 5–6: Data Integrity + Corpus Expansion

**For:** whoever has the WSL setup
**From:** Aryan's machine (no WSL — see "Why this needs you" below)
**Date:** 2026-08-11
**Branch:** `main`

---

## TL;DR — what I need from you

Everything in this session is **code + corpus + docs**. None of it has been run end-to-end,
because mutmut needs WSL and this machine doesn't have it.

```bash
git pull
cd services/mutation-testing
pip install -r requirements.txt        # deps changed — pytest and httpx were missing

python scripts/verify_corpus.py        # ~10s, no mutmut needed
python -m pytest tests/ -q             # expect 130 passed, 1 skipped ON YOUR MACHINE
python scripts/collect_dataset.py      # THE BIG ONE — 30-60 min, needs mutmut
python src/ml/train.py                 # only if collection's gates pass
```

Then tell me the numbers from the last two. Specifically I want to know whether the
**unclassified-operator rate lands under 5%** (it was 73.5%) and whether the
**declared-tier vs. measured-score** check passes for all 10 corpus modules.

> **Heads up:** `git pull` will delete your local `services/mutation-testing/storage/mutants.db`.
> That is intentional — see §5. The old data is still in git history and archived at
> `storage/archive/mutants_v1_2026-08-11.db`. You will regenerate it in step 4.

---

## 1. Why this needs you

WSL is not installed on Aryan's machine (verified: `wsl --status` reports "not installed",
even unsandboxed; no distro in the registry). mutmut has **no native Windows support**, so
nothing that generates mutants could be executed here.

What that means for trust in this work:

| | Status |
|---|---|
| Operator classifier, line resolver, feature extractor, storage, schema | **unit-tested**, 124 tests passing on Windows |
| Corpus modules + both test suites | **verified green**, 45 weak / 158 strong tests |
| Full pipeline `/mutation/run` end-to-end | **never executed** |
| Dataset collection | **never executed** |
| Model training | **never executed** |

So: the logic is tested, the integration is not. That's exactly what your run proves.

---

## 2. The bug that forced all of this

Before writing any code I checked the actual repo state against the Days 1–4 summary. The
summary said the next priority was expanding the corpus to 15–20 AI-generated modules.
That would have been premature, because **the data being collected was wrong, not just
small**.

### 2.1 mutmut line numbers were function-relative (the blocker)

`mutmut show` renders the **isolated function** as its own module and diffs that
(`mutmut/__main__.py:1691-1714`):

```python
orig_code, mutant_code = (cst.Module([function]).code.strip() for function in functions)
unified_diff(orig_code.split("\n"), mutant_code.split("\n"), ...)
```

So every `@@` hunk offset is 1-based **from the function's first line**, not from the file.
v1 stored that offset verbatim as the source line.

Measured against the old corpus:

| Function | Real file lines | v1 stored |
|---|---|---|
| `is_eligible_for_free_shipping` | 10–11 | **1, 2** |
| `count_vowels` | 12–18 | **2, 3, 5, 6** |
| `withdraw` | 1–4 | 2, 3, 4 ✓ *by coincidence* |
| `apply_discount` | 1–7 | 2–7 ✓ *by coincidence* |

Only functions that happened to be **first in their file** were ever right.

Three things consumed that number:
1. **The coverage gate** — `covered = m.line_number in covered_lines` compared a
   function-relative offset against file-absolute coverage data. Every covered/uncovered
   decision for a non-first function was arbitrary. The 13 `uncovered` rows in v1 are junk.
2. **The AST context flags** (`in_conditional`/`in_loop`/`in_exception`) — read the wrong line.
3. **The `location`** shown to the user — pointed at the wrong code.

### 2.2 The 155 stored rows were 58 distinct mutants

Three tests in `test_main.py` each POST `/mutation/run`, and `storage/db.py` fixed its path
at import time so there was no way to redirect it. Every `pytest` run appended to the real
dataset — **13 distinct `run_id`s** in the DB, one per invocation.

| Copies of the same mutant | Mutants |
|---|---|
| 8× | 6 |
| 3× | 27 |
| 2× | 1 |
| 1× | 24 |

`train_test_split` shuffles **rows**, so byte-identical duplicates landed on both sides of
the split. **The reported 73.3% validation accuracy was partly measured on memorised
training rows.**

### 2.3 73.5% of operators were `unknown`

114 of 155. Three of the eight taxonomy slots were *structurally unreachable*:

- `statement_deletion` and `return_value_replacement` — **mutmut 3.7.0 implements neither**
  (they're mutpy/cosmic-ray operators). Verified against mutmut's dispatch table.
- `null_check_removal` — its rule searched for `''`, but mutmut emits `""`
  (`mutators.py:237`). Could never match.

The classifier was also structurally wrong: it joined *all* removed lines into one string
and *all* added lines into another, then ran ordered regexes. A `-` anywhere in the removed
block matched the arithmetic rule regardless of what actually changed.

### 2.4 Structural features were a module fingerprint

`complexity` and `ast_depth` were computed over the **whole module**, so every mutant in a
file carried identical values. Across 155 rows there were exactly **4 distinct
`(complexity, ast_depth)` pairs** — one per source file. They *were* a module ID the model
could memorise.

### 2.5 The arid filter matched context lines

`ARID_PATTERNS` were searched against the **whole diff, context included**. One
`logging.info(...)` anywhere nearby flagged every mutant in that function as equivalent.
It also carried a `docstring` pattern — but mutmut never mutates docstrings
(`operator_string` returns early for triple-quoted strings, `mutators.py:40-43`), so that
pattern could only ever produce false positives. The 35 `equivalent_suspected` rows are an
overcount of unknown size.

---

## 3. What changed

### Taxonomy v2 — derived from mutmut's source, not from guessing

I pulled the mutmut 3.7.0 wheel and transcribed its actual dispatch table
(`mutmut/mutation/mutators.py:252-268`) into
[`docs/Research/experiments/mutmut_3.7_operator_catalogue.md`](docs/Research/experiments/mutmut_3.7_operator_catalogue.md).

That lets the paper say *"our taxonomy is a total function over mutmut 3.7.0's mutation
table"* instead of *"we pattern-matched some diffs"*.

**23 fine-grained operators** (drive the explanations) grouped into **8 families** (the ML
one-hot — a 23-wide one-hot is nearly all zeros at a few hundred rows).

New files:
- `src/mutators/taxonomy.py` — single source of truth. `generator`, `prioritizer`,
  `test_gap` and `attribution` all import from it, so a taxonomy edit can't leave one out
  of sync. There's a test asserting the template dicts and the operator list are the same set.
- `src/mutators/classifier.py` — lexes the changed lines with `tokenize`, aligns them with
  `difflib`, keys on the **minimal token substitution**. 45 tests, no mutmut required.
- `src/mutators/line_resolver.py` — fixes §2.1. Locates the mutated line **by content**
  within the enclosing function's span, uses the hunk offset only to disambiguate duplicates.
  Returns `None` rather than guessing — a wrong line silently corrupts the dataset, a
  missing one is visible.

Also handles mutmut's class-method mangling (`xǁClassǁmethod`, U+01C1 separator), which the
old parser would have mangled — relevant now that the corpus has a class.

### Dataset schema v2

- `dataset_schema_version` on every row; `train.py` filters on it. Kills the class of bug
  where a taxonomy change silently poisons a training set.
- Real `module_key` (`corpus/02_discount_pricing/discount_pricing.py`) + `module_slug`.
  v1 stored `source_path.name` = `"module.py"` for all three modules.
- `UNIQUE(dataset_schema_version, module_key, suite, mutant_id)` + `INSERT OR REPLACE` —
  re-collection is idempotent.
- `db_path()` resolves from `MUTATION_DB_PATH` **per call**, so `tests/conftest.py` can
  redirect the whole session to a temp file. This is what makes §2.2 impossible to repeat.
- Triage `confidence` and `reason_code` are now persisted (v1 computed and discarded them,
  despite the docstring promising they "stay auditable").

### Training pipeline

- **Population fixed.** v1 trained on all rows including `uncovered`. But `covered = 0`
  means the line never runs, which means the mutant can't be killed — the label was
  deterministically readable from one feature. It was also a train/serve mismatch, since
  inference only ever sees covered, non-arid survivors. Now trains on exactly the inference
  population.
- **`GroupKFold` by `module_key`**, mean ± std across folds.
- **Real metrics**: precision, recall, F1, ROC-AUC, PR-AUC, Brier + three baselines
  (majority class, `covered` alone, plain logistic regression).
- **`model_meta.json`** is emitted and **committed** (`model.json` stays gitignored — it's a
  build artifact). `load_model()` rebuilds the one-hot from the *persisted* category list
  and raises on mismatch, so train/inference drift is structurally impossible.

### Other fixes

- **Explanations no longer gated on the model.** `main.py` had
  `generate_missing_edge_case(...) if _ML_MODEL else None` — but templated NLG is
  deterministic. On a fresh checkout (no `model.json`) every mutant returned
  `missing_edge_case: None`.
- **Coverage tracker fails loud.** It returned `set()` on any JSON parse failure, which is
  indistinguishable from "nothing is covered" — and an empty coverage set routes *every*
  mutant to `uncovered`, producing a run that looks finished and is entirely wrong.
- **Three scores instead of one** (schema 1.1, additive so Governance isn't broken):
  `mutation_score` (killed/total), `mutation_score_adjusted` (equivalents removed),
  `test_strength` (killed / (killed + active survivors)). Formulas ship in the payload.
- **Function-scoped features** replacing the file-scoped ones from §2.4, plus
  `n_covering_tests` via coverage dynamic contexts.
- `attribution.py` (SHAP) was **imported by nothing**. Now called per scored survivor and
  written into `reports/run_<id>.json`.
- Parallelised `mutmut show` with a `ThreadPoolExecutor` — it was one subprocess per mutant,
  which is ~1,600 spawns on a 20-module run.
- `requirements.txt`: added **`pytest` and `httpx`** — `httpx` is required by FastAPI's
  `TestClient` and was an undeclared dependency the entire test suite already relied on.

---

## 4. The corpus

10 modules, each with **two** test suites:

```
corpus/<NN>_<slug>/
    <slug>.py                    # unique filename — mutmut derives mutant ids from it
    tests_weak/test_<slug>.py    # the suite under evaluation, gaps recorded in meta.json
    tests_strong/test_<slug>.py  # the oracle
    meta.json                    # tier, omitted_test_cases, provenance, sha256
```

| # | Module | Tier | Why it exists |
|---|---|---|---|
| 01 | `withdraw_ledger` | strong | migrated, unchanged |
| 02 | `discount_pricing` | medium | migrated, unchanged |
| 03 | `username_validator` | weak | migrated, unchanged |
| 04 | `grade_calculator` | strong | dense comparison boundaries, elif ladder |
| 05 | `leap_year_dates` | medium | `%`, `//`, and/or chains |
| 06 | `text_tokenizer` | weak | string methods, `in`/`not in`, `continue` |
| 07 | `permission_flags` | medium | **only source of bitwise `& \| ^ <<`** |
| 08 | `rate_limiter` | untested_function | **only class**; `__repr__` + logging arid bait; `@property` |
| 09 | `stats_summary` | weak | lambda sort key, augmented assignment |
| 10 | `order_state_machine` | medium | dict dispatch, try/except/else, break |

**Provenance: 3 human-written + 7 Claude-generated.** Modules 01–03 are your original
hand-written ones, kept **unmodified** (only the filename and its import changed). They're
the non-AI anchor — if the whole benchmark were model-generated, any finding about
"AI-generated code" would be confounded by the tool and the subject sharing a generator.
That's labelled honestly in every `meta.json` and written up in `corpus/GENERATION.md`.

### Why two suites — this is the most valuable part

Running both gives every mutant an outcome under each:

| Weak | Strong | Label |
|---|---|---|
| survived | **killed** | **true test gap** — a real missing test |
| survived | survived | likely equivalent / unkillable |
| killed | — | not a gap |

Mutation-testing research has **no ground truth** for "meaningful survivor" — `train.py`
says so in its docstring and falls back to a kill-predictor proxy. The differential oracle
gives you the actual label, cheaply, because we authored both suites deliberately. It makes
**precision@k and NDCG against real gaps** measurable, which upgrades the contribution from
"we built a ranker" to "we measured that the ranker surfaces real gaps first."

Suites are in **separate directories** because the harness copies every `test_*.py` in the
directory it's given — a strong suite sitting next to the weak one would be silently
included and destroy the experiment.

`intended_suite_tier` was declared **before** any score was measured, so
"measured vs. declared" is a validation check, not a post-hoc rationalisation.

---

## 5. About your local `mutants.db`

`git pull` will delete it. This is deliberate.

Nothing in the 155 v1 rows survives the fixes above: line numbers wrong for most functions,
73.5% of operators unlabelled under a taxonomy with unreachable categories, no module
identity, structural features that are module IDs, coverage flags derived from bad line
numbers, and 62% duplicates. Migration would mean recomputing every field from the diff —
which is exactly what re-collection does, in minutes.

It's **archived**, not lost:
- `services/mutation-testing/storage/archive/mutants_v1_2026-08-11.db`
- still in git history
- full analysis in [`docs/Research/experiments/dataset_v1_postmortem.md`](docs/Research/experiments/dataset_v1_postmortem.md)

Those numbers are genuinely useful as the "before" column of a data-quality table in the
report.

---

## 6. What to run, and what to look for

### Step 1 — corpus integrity (fast, no mutmut)

```bash
python scripts/verify_corpus.py
```

Checks every suite is **green** (mutation testing against a red suite is meaningless),
that strong ⊇ weak in test count, and that every recorded sha256 still matches. Rebuilds
`corpus/MANIFEST.json`.

Expect: `10 modules`, `provenance: {'human': 3, 'claude-opus-5': 7}`, `OK`.

If you intentionally edit a corpus module, re-run with `--update-hashes`.

### Step 2 — test suite

```bash
python -m pytest tests/ -q
```

On my machine: **124 passed, 7 skipped**. Six skips are `mutmut not installed`, one is
`no trained model`. **On yours, those 6 should run** — so expect ~130 passed, 1 skipped.

If any of those 6 fail, that's the integration gap this handoff exists to find. The most
important one is `test_line_numbers_are_absolute_file_coordinates`.

### Step 3 — collection (the long one)

```bash
python scripts/collect_dataset.py 2>&1 | tee reports/collection_$(date +%F).log
```

10 modules × 2 suites. Budget 30–60 min. Prints a per-module line as it goes so a
misconfigured module is obvious immediately rather than at the end.

**It gates before letting you train.** It will exit non-zero if:
- unclassified-operator rate **> 5%** (v1 was 73.5%)
- unresolved-line rate **> 5%**
- any module's measured weak-suite score falls outside its declared tier band

Those gates are the point. If they fail, **don't work around them** — send me
`reports/unclassified_mutations.jsonl` and I'll extend the taxonomy.

Useful flags: `--only 07_permission` to test one module, `--suites weak` to skip the oracle
run.

### Step 4 — training

```bash
python src/ml/train.py
```

Refuses below 40 rows or 4 distinct modules, because a model trained on fewer isn't
evaluable and must not be reported.

**Expect the numbers to come in BELOW the old 73.3%.** That figure was measured with
duplicate rows spanning the split (§2.2). Module-grouped CV on ~10 groups will be lower and
that is the *correct* result — report it next to the post-mortem explaining why 73.3%
wasn't real. A defensible 0.65 beats an indefensible 0.73.

---

## 7. What I want back

1. Does `pytest` go fully green with mutmut present? (the 6 currently-skipped tests)
2. Final **unclassified-operator rate** — the headline number vs. 73.5%.
3. Did the **declared-tier vs. measured-score** check pass for all 10 modules? Any module
   that's wildly off means the module or its suite is wrong, not the tool.
4. `src/ml/model_meta.json` — it has the full CV metrics and baselines.
5. `reports/unclassified_mutations.jsonl` if the gate tripped.

---

## 8. Known gaps (open, not hidden)

- **10 corpus modules, not the 15–20 planned.** Every operator family is represented, but
  several by the minimum viable number of modules. Remaining specs (retry/backoff, CSV
  parsing, BFS, matrix ops, unit conversion, config merge, roman numerals) are written up
  in the plan.
- **Only 3 human-written anchors**, all short and all by the same authors. Enough to look
  for a gross difference, not to test one.
- **Tier 1 (bytecode) and Tier 2 (dynamic) equivalent detection are not built.** Static
  Tier 0 only.
- **`match_case_removal` has no training signal** — no corpus module uses `match`.
- **`stable_test_ids` is declared but never read.** It has a `# not yet consumed` comment
  so it doesn't read as implemented. Don't report it as done.
- **No benchmarking or ablations yet.** The held-out split is *possible* now that
  `module_key` exists — it wasn't before — but hasn't been run. That's the next step after
  your collection succeeds.
- **The empirical half of the mutmut catalogue is missing.** I transcribed the authoritative
  dispatch table from source, but the "dump real diffs and cluster them by frequency" half
  needs mutmut. Worth doing on your machine for the report's appendix.

---

## 9. Reference

| Document | What it's for |
|---|---|
| [`services/mutation-testing/README.md`](services/mutation-testing/README.md) | Service overview, WSL setup, API, taxonomy table |
| [`corpus/GENERATION.md`](services/mutation-testing/corpus/GENERATION.md) | Corpus provenance + the reproducibility statement for the paper |
| [`docs/Research/experiments/mutmut_3.7_operator_catalogue.md`](docs/Research/experiments/mutmut_3.7_operator_catalogue.md) | Verbatim transcription of mutmut's mutation table |
| [`docs/Research/experiments/dataset_v1_postmortem.md`](docs/Research/experiments/dataset_v1_postmortem.md) | Every v1 measurement, for the "before" column |
| [`docs/Architecture/ADR-002-operator-taxonomy-v2.md`](docs/Architecture/ADR-002-operator-taxonomy-v2.md) | Why the taxonomy is derived from source |
| [`docs/Architecture/ADR-003-dataset-schema-versioning.md`](docs/Architecture/ADR-003-dataset-schema-versioning.md) | Why v1 was purged and how versioning prevents a repeat |
