# ADR-002: Operator taxonomy derived from mutmut's source, not from observed diffs

**Status:** accepted · **Date:** 2026-08-11 · **Supersedes:** the implicit v1 taxonomy

## Context

The v1 operator taxonomy was assembled by pattern-matching diffs that mutmut happened to
produce on a three-module corpus. Measured against the committed dataset, it classified
**114 of 155 mutants (73.5%) as `unknown`**.

Three separate defects, each verified:

1. **Structurally wrong matching.** `_guess_operator` joined *all* removed lines into one
   string and *all* added lines into another, then ran ordered regexes over the pair. A
   `-` anywhere in the removed block matched the arithmetic rule regardless of what had
   actually changed.

2. **Unreachable categories.** `statement_deletion` and `return_value_replacement` were
   declared but mutmut 3.7.0 implements neither — they belong to mutpy/cosmic-ray.
   `null_check_removal` searched the added text for `''` while mutmut's
   `operator_assignment` emits `""` (`mutators.py:237`), so it could never match. Three
   of eight one-hot slots were permanently zero.

3. **Missing the highest-volume operators.** No rule existed for call-argument
   replacement/removal, numeric-literal increment, string wrapping, assignment-to-`None`,
   or boolean flips — collectively the majority of what mutmut generates on ordinary code.

The practical consequence: the one-hot operator feature was mostly noise, and every
survivor received the same generic "not recognised by our taxonomy" explanation.

## Decision

**Derive the taxonomy from mutmut's own dispatch table, and classify at the token level.**

mutmut is pure Python and its mutation rules are literal dicts. `mutation_operators`
(`mutmut/mutation/mutators.py:252-268`) is a complete, closed list of fifteen node-type →
operator-function pairs. It is transcribed verbatim into
[`mutmut_3.7_operator_catalogue.md`](../Research/experiments/mutmut_3.7_operator_catalogue.md),
with the wheel it came from recorded.

Taxonomy v2 is a **total function over that table**: 23 fine-grained operators, every one
traceable to a specific mutmut operator function, and nothing declared that mutmut cannot
produce.

Classification uses `tokenize` + `difflib.SequenceMatcher` to extract the *minimal token
substitution* between the removed and added line, then keys on that. This mirrors
mutmut's own model — it mutates one CST node at a time — so a token-level diff recovers
the operator almost exactly.

**Two levels, not one:**

- **23 operators** drive the user-facing explanation, where precision is the entire point.
  "The constant is never checked exactly" and "this argument is never exercised as `None`"
  are different missing tests and deserve different advice.
- **8 families** form the ML one-hot. At a few hundred training rows a 23-wide one-hot is
  nearly all zeros and trains badly; the families stay populated.

## Alternatives considered

**Extend the v1 regex list.** Rejected: the matching was structurally wrong, not merely
incomplete, and the resulting taxonomy would still have been a set of heuristics that
happened to fit one sample — indefensible in the write-up.

**Parse mutants with libcst and diff the CST.** More precise in principle, but it means
re-parsing every mutant and reimplementing mutmut's own transformation to identify it.
The token-level diff already resolves the entire observed catalogue; this is the
cheaper equivalent.

**Use `mutants/<module>.py` directly instead of `mutmut show`.** mutmut writes every
mutant into one generated file, which would avoid ~1,600 subprocess spawns on a 20-module
run. Deferred: a real improvement, but it changes the parsing path at the same time as
the taxonomy, and one risky change at a time is enough. A `ThreadPoolExecutor` around the
`show` calls captures most of the speedup for none of the risk.

## Consequences

**Good.** The taxonomy can be stated in the paper as a total function over mutmut
3.7.0's mutation table, with a citation, instead of "we pattern-matched some diffs". The
classifier is a standalone module with 45 tests that need neither mutmut nor WSL, so it
can be iterated on any machine. Unclassified mutants are logged to
`reports/unclassified_mutations.jsonl` and gate the collection run at 5%.

**Costs.** The taxonomy is pinned to mutmut 3.7.0 — hence the exact pin in
`requirements.txt`. A mutmut upgrade requires re-reading `mutators.py` and re-checking the
catalogue. This is the right trade: the alternative is a taxonomy that drifts silently.

**Breaking.** Every v1 operator label is invalid. Combined with ADR-003, this is part of
why dataset v1 was purged rather than migrated.

## Verification

- `tests/test_classifier.py` — 45 tests, one per operator plus the v1 regression (a pure
  comparison change must not classify as arithmetic).
- `tests/test_explainability.py` — asserts `TEMPLATES`, `STUB_TEMPLATES` and `OPERATORS`
  are the same set, so a taxonomy edit cannot silently drop an explanation.
- The collection gate fails above 5% `other`.
