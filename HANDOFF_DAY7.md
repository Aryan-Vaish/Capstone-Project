# Handoff — Day 7: your gate failure was real, and it found a genuine bug

**For:** whoever ran `collect_dataset.py`
**Date:** 2026-08-13
**Supersedes:** the "what to run" section of `HANDOFF_DAY5-6.md`

---

## First: thank you, and the two gates that passed matter more than the one that failed

| Gate | Your result | What it proves |
|---|---|---|
| Unclassified operators | **0.1%** (was 73.5%) | Taxonomy v2 works on real mutmut output |
| Unresolved line numbers | **0%** | The content-based line resolver works |

Those were the two hard fixes from Day 5, and they are now validated against real execution
rather than unit tests. That is the substantive result of your run.

**The third gate failing was correct behaviour.** It was pointing at a real defect — just
not the one I first guessed.

---

## ⚠ If you hit `table mutants has no column named removed_line`

That was my bug and it is fixed — **`git pull` and re-run**. No other action needed.

Day 7 added two columns without a migration. `init_db()` runs `CREATE TABLE IF NOT EXISTS`,
which does nothing once the table exists, so your database never gained them and every
INSERT failed. `init_db` now compares the live table against the schema and `ALTER TABLE
ADD COLUMN`s whatever is missing, printing a line when it does.

**Nothing was lost.** Every module failed at the INSERT, so your 670 rows are intact, and
the migration is non-destructive (`tests/test_storage_migration.py` pins that).

Why 183 tests passed while this broke: `conftest.py` gives every test session a *fresh*
temp database, so `init_db` always built the table from scratch at the current schema. No
test in the suite ever opened a pre-existing database — a migration bug was structurally
invisible. That gap is now closed by 9 tests that build their own legacy databases.

---

## What I need you to run

Three steps. **Step 1 needs no mutmut and takes seconds.**

```bash
git pull
cd services/mutation-testing

# STEP 1 -- against your EXISTING 670-row database. No re-collection.
python scripts/analyze_collection.py --json-out
#   -> send me reports/analysis_<date>.json

# STEP 2 -- re-collect with the narrowed filter (~30-60 min)
python scripts/collect_dataset.py 2>&1 | tee reports/collection_$(date +%F).log

# STEP 3 -- the after numbers
python scripts/analyze_collection.py --json-out
```

**Step 1 genuinely must come first.** Your existing database is the only record of how the
*old* filter behaved on real data — it is the "before" half of the evidence that the fix
worked. Re-collecting upserts over those rows and replaces their `triage_status` values.

Belt and braces: step 2 now **auto-archives** the database to
`storage/archive/mutants_pre_<timestamp>.db` before it writes anything, and prints the
path. So if the order slips again, the data is still recoverable — but running step 1
first is still what gets me the numbers fastest.

If your `mutants.db` from the first run is gone, skip to step 2; we just lose the
before/after comparison.

---

## What I got wrong, and how your data caught it

My first hypothesis: the gate compares `killed / total`, and `total` includes
`equivalent_suspected` mutants which no test can kill — so string-heavy modules face a
ceiling below their own band floor. Fix: gate on `mutation_score_adjusted` instead.

**That was wrong, and shipping it would have made things worse.**

1. **The mechanism doesn't exist.** `collect_dataset.py` decides `killed` *before* it
   consults triage. The equivalence filter has **zero** effect on `mutation_score`. My
   story required the filter to move a number it cannot touch.

2. **The filter is the actual bug.** I ran the triage rule against real mutations from your
   corpus. **6 of 7 were mislabelled:**

   | Mutation | Old verdict | Reality |
   |---|---|---|
   | `text.split(" ")` → `split("XX XX")` | equivalent | changes *every* tokenizer result |
   | `STOP_WORDS = {"the"}` → `{"XXtheXX"}` | equivalent | changes tokenizer output |
   | `"new": ("paid",)` → `"XXnewXX"` | equivalent | breaks state-machine dispatch |
   | `return "A"` → `return "XXAXX"` | equivalent | **04's weak suite asserts on this** |
   | `raise ValueError("Bad score")` | equivalent | ✅ actually arid |

   The rule blanked out every string literal and compared line "skeletons", so *any*
   single-line string change matched — dictionary key or log message, no distinction.
   Data strings are among the **most** killable mutants in a corpus, not the least.

3. **So the fix I proposed would have hidden real failures.** `mutation_score_adjusted`
   subtracts exactly that mislabelled set from its denominator. Adopting it would have
   laundered genuine weak-suite gaps into an uncounted bucket — the precise inflation this
   project exists to avoid. It was also *monotone in our favour* (adjusted ≥ raw, always),
   which is the shape of moving the goalposts whatever the intent.

**The filter is now narrowed: 7/7 correct, up from 1/7.** A string change is `active`
unless the literal sits in message position (`raise`, logging, `warnings.warn`, `print`) or
the enclosing function is `__repr__`/`__str__`.

**This lowers scores.** That is the point, and it is why it can't be called goalpost-moving.

---

## The tier bands were separately broken

Independent of all the above, and visible without looking at any of your numbers:

- **They overlapped.** `strong` 0.80–1.01 and `medium` 0.45–0.85 intersect on [0.80, 0.85];
  `medium` and `weak` 0.10–0.60 on [0.45, 0.60]. A score of 0.50 satisfied **two tiers at
  once**, so the gate could never discriminate adjacent tiers.
- **They contradicted `GENERATION.md`**, which pre-registers 85–95 / 55–70 / 25–40.
- I invented them and justified them nowhere.

They're replaced by an exact **Jonckheere–Terpstra** trend test on the one thing genuinely
pre-registered — the *ordinal* tier labels. It asks only: do scores increase across
`weak < medium < strong`? With 9 ranked modules split 3/4/2 it enumerates all 1260
partitions for an exact p-value (floor ≈ 0.0008).

Why this is better: it **invents no numbers**, so nothing can be fitted to the data it
validates. And being rank-based, it gives the same verdict under either score metric —
so the choice of metric stops being a way to change the answer.

---

## What step 1's report will tell us

The decisive section is **§2, equivalence-filter precision**:

```
  reason code                 flagged  killed  precision  95% CI
  string_literal_only             110      56      0.491  [0.399, 0.583]
```

A mutant the filter called unkillable that your suite then **killed** is a proven false
positive — no oracle needed, no assumptions. It also dumps 15 sample rows so you can see
*which* literals were mislabelled.

- **Precision well below 0.85** → my diagnosis holds, the narrowing is justified, proceed
  to step 2.
- **Precision near 1.0** → I'm wrong again, the filter is fine, and the tier failures have
  another cause entirely. **Stop and tell me** — I'd rather re-diagnose than ship a fix for
  a problem you don't have.

The report also covers things the log couldn't:

- **Integrity** — cross-suite mutant-set symmetry (mutmut's `timeout`/`suspicious` statuses
  are silently dropped by our parser, which would quietly break every weak↔strong join),
  and a `killed ∧ ¬covered` check (a logical contradiction that would mean the line
  resolver is still mapping some mutants wrongly).
- **Oracle adequacy** — is the strong suite actually strong enough to be an oracle? Every
  gap label depends on it and it has never been measured.
- **Gap labels** — how many *true gaps* the weak/strong pairing yields. This is the sample
  size that constrains every future precision@k claim, and I expect it to be small.
- **A three-valued tier verdict** — `OK` / `BELOW_FLOOR` / `ABOVE_CEILING`. I've registered
  a prediction while blind to your numbers: **every failure was below floor**. If any
  module failed *above* its ceiling, my explanation is wrong for that module. `06_text_tokenizer`
  is the one to watch — it's tiered `weak` with a 0.10 floor, and 4 tests dropping under a
  10% kill rate seems unlikely.

---

## One improvement that saves you future runs

`removed_line` and `added_line` are now stored per mutant. Triage becomes **replayable
offline** — so after step 2, I can iterate on filter rules on Windows with zero mutmut.

Step 2 should be the **last** full collection needed for any future triage change.

---

## Expect scores to drop

Mutants previously excluded as "equivalent" re-enter the denominator as the real survivors
they always were. That is the fix working, not a regression.

Training stays blocked until the new **Gate 4** (filter precision ≥ 0.85) passes. The
reason is that `mutation_score_adjusted` subtracts the filter's own output from its
denominator — so without measuring the filter, the pipeline would be grading itself, and an
over-flagging filter would be *rewarded* with a higher score.

---

## Status on my side

186 tests passing (up from 124), 7 skipped here — all mutmut- or model-gated, and on your
machine 6 of those 7 run. Your last run's 183 passed / 1 skipped means **every mutmut
integration test passed against real execution**, including
`test_line_numbers_are_absolute_file_coordinates`. That is the Day 5 line-resolver fix
confirmed end to end, which is worth more than the gate that failed.

| New | Purpose |
|---|---|
| `tests/test_triage.py` (26) | Regression guard: the 8 mislabelled corpus mutations must stay `active` |
| `tests/test_storage_migration.py` (9) | The migration bug above; builds legacy databases the session fixture never would |
| `tests/test_collection_stats.py` (15) | Jonckheere–Terpstra + Wilson intervals |
| `tests/test_analyze_collection.py` (10) | Diagnostic tested against a synthetic DB with planted faults |
| `scripts/analyze_collection.py` | The read-only report you're about to run |
| `src/evaluation/stats.py` | Exact trend test + Wilson, stdlib only |
| `docs/Architecture/ADR-004-*.md` | Full reasoning, including the disclosure wording for the report |

The corpus is untouched — all 10 modules, 45 weak / 158 strong tests, still green.

Your `verify_corpus.py --update-hashes` was a harmless no-op, incidentally: Day 7 changed
only `corpus/GENERATION.md`, which is not hashed.
