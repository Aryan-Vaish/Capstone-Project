# Corpus Generation and Provenance

**Corpus version:** v2 · **Modules:** 10 · **Assembled:** 2026-08-11

This document records how each corpus module came to exist, so the benchmark can be
described accurately in the write-up rather than gestured at. `MANIFEST.json` is the
machine-readable index; `scripts/verify_corpus.py` regenerates it and re-checks every
hash and test suite.

---

## 1. What "reproducible" can and cannot mean here

Seven of the ten modules were written by a large language model. A sampling model does
not produce byte-identical output when re-run, so **this corpus is not reproducible in
the sense of "run a script and get the same files back"**, and claiming otherwise would
be false.

What *is* reproducible, and what the write-up should say:

- the **specification** for every module is published (§4 below and each `meta.json`);
- the **artifact** itself is committed;
- the **sha256** of every file is recorded in `meta.json` and re-verified by
  `verify_corpus.py`, so any later edit is detectable;
- the **collection procedure** over the artifact (`scripts/collect_dataset.py`) is fully
  deterministic given the corpus.

State this limitation explicitly in the methods section instead of implying determinism.

## 2. Provenance split, and why it matters

| Provenance | Modules | Count |
|---|---|---|
| Human-written | `01_withdraw_ledger`, `02_discount_pricing`, `03_username_validator` | 3 |
| `claude-opus-5` (in-session) | `04` – `10` | 7 |

The three human modules were hand-written by the project team during Days 1–3 and are
retained **unmodified** (only the filename and its import changed, for the module-identity
fix). They are the corpus's non-AI anchor.

**The validity threat this addresses.** If every benchmark module were produced by the
same model family the tooling is being evaluated with, any finding about "AI-generated
code" would be confounded — the tool and the subject would share a generator. Keeping
genuine human-written modules lets you check whether mutation scores, survivor rates and
operator distributions differ systematically between the two groups. That comparison is
itself a reportable result, and it is directly on-thesis for a project about trustworthy
AI-generated software.

**Report this threat regardless of the outcome.** Three human modules is an anchor, not a
control group; the sample is too small for a significance claim. The honest framing is
"we observed X across 3 human vs 7 AI-generated modules" with the sample size stated,
plus a note that expanding the human set is future work.

## 3. Design constraints the corpus had to satisfy

The corpus is a **benchmark**, so each reportable dimension had to vary independently
rather than co-vary with module identity.

**Control flow.** Across the ten modules: guard clauses, `elif` ladders, nested
conditionals, `for` with `continue` and `break`, `while` loops, `try`/`except`/`else`,
dict dispatch, comprehensions, `lambda` sort keys, a stateful class with `@property` and
`@staticmethod`, and bitmask arithmetic.

**Operator opportunity.** Every taxonomy-v2 *family* must be produced by at least three
modules, or its one-hot slot has no training signal. The previous three-module corpus
produced **zero** instances of bitwise operators, string-method swaps, lambda bodies,
loop-control statements, and `match` arms — five families with nothing to learn from.
`07_permission_flags` exists specifically to supply bitwise and shift mutations;
`06_text_tokenizer` supplies string methods and loop control; `09_stats_summary` supplies
the lambda body.

**Arid bait.** Tier-0 equivalent triage needs cross-module signal. In the original corpus
only `01` exercised it at all. `08_rate_limiter` deliberately contains a `__repr__`, two
`logging` calls, and error-message strings that no reasonable suite asserts on.

**Suite strength, declared before measurement.** Every module's `intended_suite_tier` was
fixed when it was authored, *before* any mutation score was computed:

| Tier | Expected weak-suite kill rate | Modules |
|---|---|---|
| `strong` | 85–95% | `01`, `04` |
| `medium` | 55–70% | `02`, `05`, `07`, `10` |
| `weak` | 25–40% | `03`, `06`, `09` |
| `untested_function` | contains functions with no tests at all | `08` |

Declaring the tier in advance turns "measured score vs. intended tier" into a validation
figure. A module declared `strong` that measures 0.3 means the module or its suite is
wrong — not that the tool found something. That check is far more persuasive than a bare
table of scores, and it is only available because the tier was written down first.

## 4. Per-module specification

Each `meta.json` carries the full record. The load-bearing field is
**`omitted_test_cases`** — an explicit list of what the weak suite deliberately does not
test. It is the ground-truth annotation: a surviving mutant that corresponds to a listed
omission is a true positive for the explainability layer, and one that does not is worth
inspecting.

Example (`03_username_validator`):

```json
"intended_suite_tier": "weak",
"omitted_test_cases": [
  "username of exactly 3 and exactly 20 characters",
  "usernames containing punctuation or spaces",
  "underscore-only username",
  "empty string",
  "uppercase vowels in count_vowels",
  "count_vowels with zero vowels, and multi-vowel accumulation"
]
```

## 5. The weak/strong suite pairing

Every module ships **two** suites:

- `tests_weak/` — the suite as a developer would plausibly have written it, with
  specific gaps recorded in `omitted_test_cases`. This is the suite under evaluation.
- `tests_strong/` — the oracle. Intended to kill every non-equivalent mutant.

They live in separate directories rather than side by side because the harness copies
every `test_*.py` it finds in the given directory; a strong suite sitting next to the
weak one would be silently included in normal runs and destroy the experiment.

**Why this pairing is the most valuable thing in the corpus.** Running both gives every
mutant an outcome under each, which yields a real label for the question the ranker
actually answers:

| Weak suite | Strong suite | Label |
|---|---|---|
| survived | **killed** | **true test gap** — what a developer should go and fix |
| survived | survived | likely equivalent or unkillable — noise |
| killed | — | not a gap |

Mutation-testing research has no ground truth for "meaningful survivor"; `ml/train.py`
therefore falls back to a kill-predictor proxy, and says so. The differential oracle
replaces the proxy with a real label, cheaply, precisely because both suites were
authored deliberately. It makes **precision@k and NDCG against real gaps** measurable,
which upgrades the claim from "we built a ranker" to "we measured that the ranker
surfaces real gaps first".

Current suite sizes: 45 weak tests, 158 strong tests, all green
(`scripts/verify_corpus.py`).

## 6. Adding a module

1. Create `corpus/<NN>_<slug>/` with `<slug>.py`, `tests_weak/test_<slug>.py`,
   `tests_strong/test_<slug>.py`.
   The filename must be unique across the corpus and must not start with a digit —
   mutmut derives mutant ids from the module's import name, so duplicate filenames
   collide the moment two modules share a function name. This is exactly what the
   original all-`module.py` corpus did wrong.
2. Write `meta.json`, including `intended_suite_tier` and `omitted_test_cases`
   **before** measuring anything.
3. Run `python scripts/verify_corpus.py` — it re-hashes, runs both suites, and rebuilds
   `MANIFEST.json`.
4. Re-run collection. The dataset's uniqueness constraint makes this idempotent.

## 7. Known gaps

- **Ten modules, not the 15–20 originally planned.** Families are all represented, but
  several are covered by the minimum viable number of modules. The remaining specs
  (date arithmetic, retry/backoff, CSV parsing, BFS, matrix ops, unit conversion,
  config merge, temperature banding, roman numerals) are written up in the Day 5–6 plan
  and are the natural next increment.
- **Only 3 human-written anchors**, all short and all authored by the same team early in
  the project. Enough to look for a gross difference, not enough to test one.
- **No module exercises `match` statements**, so `match_case_removal` still has no
  training signal.
- Every module is self-contained with no external dependencies, no I/O and no
  concurrency — which keeps mutation runs fast and deterministic, but means the corpus
  says nothing about how the pipeline behaves on code with real side effects.
