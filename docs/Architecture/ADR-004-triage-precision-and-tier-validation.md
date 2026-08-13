# ADR-004: Narrow the equivalence filter; replace tier bands with an ordinal trend test

**Status:** accepted · **Date:** 2026-08-13 · **Amends:** ADR-003's collection gates

## Context

The first real collection run — 670 mutants, 10 modules × 2 suites, executed on a
collaborator's WSL machine — passed two of three quality gates convincingly:

| Gate | Result | What it validates |
|---|---|---|
| Unclassified operators | **0.1%** (was 73.5%) | Taxonomy v2 (ADR-002) works on real mutmut output |
| Unresolved line numbers | **0%** | The content-based line resolver works |

The third gate failed: **6 of 10 modules** fell outside their declared
`intended_suite_tier` band, including three modules authored specifically for this corpus.

## The first diagnosis was wrong, and is recorded here because it nearly shipped

The initial hypothesis was that the gate metric was at fault. It compares `killed / total`,
and `total` includes `equivalent_suspected` mutants, which are unkillable by construction —
so a module dense in string literals would face a ceiling below its own band floor. The
proposed remedy was to gate on `mutation_score_adjusted` instead.

That hypothesis was **refuted before implementation**, on two counts:

1. **The mechanism was wrong.** `collect_dataset.py` assigns `killed` *before* consulting
   triage. The arid filter therefore has **zero** influence on `mutation_score`. The
   ceiling story required the filter to affect a number it cannot touch.

2. **The filter was badly wrong in a way that made the proposed fix actively harmful.**
   Running `triage_mutant` against real corpus mutations, **6 of 7 were mislabelled**:

   | Mutation | v1 verdict | Reality |
   |---|---|---|
   | `text.split(" ")` → `split("XX XX")` | equivalent | changes every tokenizer result |
   | `STOP_WORDS = {"the"}` → `{"XXtheXX"}` | equivalent | changes tokenizer output |
   | `"new": ("paid",)` → `"XXnewXX"` | equivalent | breaks state-machine dispatch |
   | `return "A"` → `return "XXAXX"` | equivalent | **module 04's suite asserts on this** |
   | `raise ValueError("Bad score")` | equivalent | ✅ genuinely arid |

   `mutation_score_adjusted` subtracts precisely this set from its denominator. With ~6/7
   of it mislabelled, switching metrics would have **laundered genuine test-suite failures
   into an uncounted bucket** — the exact inflation `mutation_schema.py`'s own docstring
   says this project exists to avoid.

The proposal was also **monotone in our favour**: `adjusted ≥ raw` always. A criterion
amendment that cannot possibly make any module look worse is the shape of goalpost-moving,
regardless of intent.

## Decision

### 1. Narrow the equivalence filter (the actual defect)

`_is_string_literal_only_change` blanked out every literal and compared line skeletons, so
*any* single-line string change matched — with no regard for whether the string was a log
message or a dictionary key. Data strings are among the **most** killable mutants in a
corpus, not the least.

The rule is inverted: **a string change is `active` unless the literal is positively
identified as sitting in message position** — an argument to `raise`, a logging or warning
call, or `print` — or the enclosing function is cosmetic (`__repr__`, `__str__`). Detection
parses the changed line with `ast`; a line that cannot be parsed standalone (`"A": 4.0,`
inside a dict literal, a tuple element) correctly falls through to `active`.

Erring toward reporting a mutant is safe. Erring toward hiding one produced this bug.

Flags from the narrowed rule carry a distinct `string_literal_message` reason code, so the
new rule remains separable from the old one in already-collected data.

**This change lowers scores.** That is why it goes first, and why it cannot be
characterised as moving the goalposts.

### 2. Add Gate 4: measured filter precision

A mutant the filter called unkillable that the **same suite then killed** is a demonstrated
false positive, observable with no oracle and no cross-suite join.

Collection now fails if precision falls below **0.85**, reported per reason code with
Wilson intervals (Wald is unusable at these counts — 5–30 flags per module, with rates near
the boundaries).

This exists to answer a self-reference objection: `mutation_score_adjusted` makes the
validation depend on the pipeline's own unvalidated heuristic, so without measurement an
over-flagging filter would be *rewarded* with a higher score. The denominator is now
measured, not trusted.

### 3. Replace the tier bands with a pre-registered ordinal trend test

The bands were independently broken, for reasons visible without reference to any
measurement:

- **They overlapped.** `strong` (0.80–1.01) and `medium` (0.45–0.85) intersect on
  [0.80, 0.85]; `medium` and `weak` (0.10–0.60) on [0.45, 0.60]. A 0.50 score satisfied two
  tiers at once, so the gate could not discriminate adjacent tiers at all.
- **They contradicted the pre-registration.** `GENERATION.md` §3 declares 85–95 / 55–70 /
  25–40; the implementation used materially looser numbers.
- They were invented, and justified in prose nowhere.

Replaced by an exact **Jonckheere–Terpstra** test of the one thing genuinely pre-registered:
the **ordinal** tier label. Prediction: measured weak-suite scores increase across
`weak < medium < strong`. With the corpus's 9 ranked modules split 3/4/2, enumerating all
9!/(3!·4!·2!) = **1260** partitions gives an exact p, with a floor of p ≈ 0.0008.

Three properties make this the right instrument:

- **It invents no numbers.** Inputs are the pre-registered labels and the measured scores.
  There is nothing to fit, so nothing can be fitted to the data it validates.
- **It is rank-based**, so it returns the same verdict under `mutation_score` and
  `mutation_score_adjusted` unless the metric reorders modules. **The metric debate stops
  being a lever on the result** — which retroactively defuses the goalpost objection for
  the primary validation claim.
- It degrades gracefully: a near-miss yields an interpretable p-value rather than a binary
  six-of-ten failure.

Retained alongside it are a few **wide rails**, justified by what they detect rather than by
what we expect: adjusted score ∈ [0.05, 0.98] (outside this, suspect the harness), and
`uncovered/total > 0.5` only for the `untested_function` module. Module 08 gets a targeted
check — it must have the corpus's highest uncovered rate — instead of a band, which is far
sharper than "score fell in 0.0–0.80".

### 4. Persist the changed lines

`removed_line` and `added_line` are now stored per mutant. Triage and operator
classification become **replayable offline**, so iterating on a filter rule no longer costs
a 30–60 minute Linux-only mutmut run. Not storing them is what made this defect expensive
to diagnose.

### 5. `mutation_score_adjusted` stays reported, but does not gate

It remains in every Evidence Object. It is barred from *deciding* anything until Gate 4
demonstrates its denominator is trustworthy.

## Consequences

**Good.** The defect that actually caused the failure is fixed, and the fix is verifiable
in isolation (7/7 correct, from 1/7). The tier check no longer rests on invented numbers.
Filter iteration is now free. The false-positive rate becomes a reportable result about the
triage layer rather than a hidden assumption.

**Costs.** One further full collection run is needed to observe the narrowed filter's
effect. Scores will drop — mutants previously excluded as "equivalent" re-enter the
denominator as the real survivors they always were.

**Risk accepted.** The narrowed rule may now under-flag: a genuinely arid string outside
message position will be reported as an active survivor. That direction is deliberate — a
spurious survivor wastes a reader's time, whereas a hidden one corrupts the score.

## Disclosure (for the report's methods section)

> **Post-hoc amendment to the tier-validation gate.** Every module's `intended_suite_tier`,
> the score definitions, and the validation bands were committed in `c0a0ec9` before any
> mutation score was measured, and that commit hash is recorded on every row of the
> collected dataset (`pipeline_git_sha`) — so the ordering is verifiable from the data
> rather than asserted. The first collection nevertheless placed six of ten modules outside
> their declared band.
>
> Inspection revealed two defects. First, the Tier-0 equivalence filter classified any
> single-line string-literal change as equivalent regardless of the literal's role, so
> dictionary keys, stop-word sets, enumerated state names and returned values — behaviourally
> significant, readily killable mutants — were labelled unkillable. Second, the numeric
> bands overlapped between adjacent tiers and were inconsistent with the percentages
> pre-registered in the corpus documentation, so the gate could not discriminate adjacent
> tiers even in principle.
>
> **Both were corrected after observing that the gate failed, and this is reported as a
> post-hoc amendment.** Two mitigations apply. The filter narrowing *lowers* measured
> scores, so it cannot be characterised as relaxing a criterion to obtain a pass. And the
> replacement tier check — an exact Jonckheere–Terpstra test of the pre-registered ordinal
> tier labels — introduces no free parameters, so there is nothing that could be fitted to
> the data it validates; being rank-based, it also returns an identical verdict under
> either candidate score metric.
>
> A change from `mutation_score` to `mutation_score_adjusted` as the gating metric was
> proposed and then **rejected on evidence**: because the adjusted score subtracts the
> equivalence filter's output from its denominator, adopting it while that filter was
> over-flagging would have concealed genuine test-suite failures. An additional gate now
> measures the filter's false-positive rate directly — a mutant it declared equivalent that
> the suite then killed — and the adjusted score is reported but never used to gate until
> that rate clears a pre-set threshold. Results are reported under both metrics throughout.

## Verification

- `tests/test_triage.py` — 26 tests. The eight mislabelled corpus mutations must be
  `active`; the eight message-position ones must stay flagged. This is the regression
  guard for the whole change.
- `tests/test_collection_stats.py` — Jonckheere–Terpstra against ordered, reversed,
  shuffled and tied inputs; the rank-invariance property; Wilson at the boundaries.
- `tests/test_analyze_collection.py` — synthetic dataset with planted false positives, a
  planted asymmetric module and a planted integrity contradiction; the diagnostic must
  surface each.
- `scripts/analyze_collection.py` — read-only, no mutmut, runs anywhere. Section 2 is the
  measurement that decides whether this ADR's premise held.
