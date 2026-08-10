# Intelligent Mutation Testing Service

Generates real mutants, filters out the ones no test could ever catch, ranks the
survivors that remain, and explains — in English — what test is missing for each one.

Part of *Diagnosing Flaky Tests and Enhancing Mutation Testing to Enable Trustworthy
AI-Generated Software*.

---

## ⚠ Environment: WSL/Linux required

**mutmut has no native Windows support.** Anything that generates mutants must run under
WSL or Linux. Everything else — the operator classifier, line resolution, feature
extraction, training, and the whole test suite bar six tests — is pure Python and runs
anywhere.

```bash
# one-time, in an ELEVATED PowerShell, then reboot
wsl --install -d Ubuntu

# then, inside the distro
sudo apt update && sudo apt install -y python3.12 python3.12-venv
python3.12 -m venv ~/.venvs/mutation && source ~/.venvs/mutation/bin/activate
pip install -r requirements.txt
```

Two traps already paid for:

- **Do not use Python 3.14.** `pydantic-core` had no prebuilt wheel for it, which forces
  a Rust build. 3.12 is the target.
- **Do not create the venv on `/mnt/c`.** venv creation fails on the Windows-mounted
  filesystem. Put it under the Linux home directory.

Verify: `python3 --version` → 3.12.x, and `mutmut --help` succeeds.

---

## Pipeline

```
source + tests
   ↓  mutation_coverage/tracker.py    which lines does the suite actually execute?
   ↓  mutators/generator.py           mutmut 3.7.0 → real mutants
   ↓  mutators/classifier.py          diff → taxonomy-v2 operator
   ↓  mutators/line_resolver.py       function-relative offset → absolute file line
   ↓  triage/arid_filter.py           Tier 0: flag equivalents (never delete)
   ↓  features/extractor.py           function-scoped feature vector
   ↓  ml/prioritizer.py               XGBoost P(killed) → rank surprising survivors
   ↓  explainability/test_gap.py      "what test is missing" (templated, no model needed)
   ↓  explainability/attribution.py   "why did the model rank this" (SHAP, → run report)
Evidence Object
```

## Status

| Component | State |
|---|---|
| Real mutant generation + isolated execution | done |
| Coverage gating | done |
| Tier-0 equivalent triage | done (Tier 1/2 are future work) |
| Dataset assembly (SQLite, schema v2) | done |
| ML prioritizer | code done — **awaiting a collected dataset** |
| Explainability (templated + SHAP) | done |
| Corpus | 10 modules, weak+strong suites, all green |
| Benchmarking / ablations | not started |

**Not yet run end-to-end on this machine**: collection and training need mutmut, i.e.
WSL. See "Reproduce from scratch" below.

## Quick start

```bash
cd services/mutation-testing

python scripts/verify_corpus.py           # any platform; run before collecting
python -m pytest tests/ -q                # any platform (6 tests skip without mutmut)

# WSL only:
python scripts/collect_dataset.py         # both suites over the whole corpus
python src/ml/train.py                    # grouped CV + model + model_meta.json
python -m uvicorn src.main:app --port 8002 --reload
```

## API

**`GET /health`** — also reports whether the loaded model is stale relative to the
code's taxonomy, so an absent or mismatched model is visible immediately rather than
inferred from silently-`None` scores.

**`POST /mutation/run`**

```json
{
  "source_file": "corpus/02_discount_pricing/discount_pricing.py",
  "tests_dir": "corpus/02_discount_pricing/tests_weak",
  "suite": "weak"
}
```

## The three scores

Reporting one "mutation score" is ambiguous, and the ambiguity flatters the tool. Three
numbers are published every run, with their formulas included in the payload
(`score_definitions`), using Pitest's vocabulary so they are comparable to published
results:

| Field | Formula | Meaning |
|---|---|---|
| `mutation_score` | `killed / total` | Mutation coverage. Uncovered mutants stay in the denominator **on purpose** — an untested line is a real gap. |
| `mutation_score_adjusted` | `killed / (total − equivalent_suspected)` | Classical mutation score, with suspected equivalents removed. |
| `test_strength` | `killed / (killed + active_survivors)` | Suite quality on code the suite actually reaches. |

## Operator taxonomy v2

Transcribed from mutmut 3.7.0's own dispatch table
(`docs/Research/experiments/mutmut_3.7_operator_catalogue.md`), not inferred from
observed diffs. **23 fine-grained operators** drive the explanations; **8 coarse
families** form the model's one-hot vector, because a 23-wide one-hot is nearly all
zeros at a few hundred training rows.

| Family | Operators |
|---|---|
| `comparison` | `comparison_boundary`, `equality_negation` |
| `logical` | `logical_connector_swap`, `unary_operator_removal`, `identity_negation`, `membership_negation`, `boolean_constant_flip` |
| `arithmetic` | `arithmetic_replacement`, `bitwise_replacement`, `augmented_assign_swap`, `augmented_assign_removal` |
| `constant` | `numeric_constant_change`, `string_literal_change`, `name_replacement` |
| `call` | `argument_replacement`, `argument_removal`, `string_method_swap`, `dict_keyword_change` |
| `assignment` | `assignment_to_none`, `lambda_body_replacement` |
| `control_flow` | `loop_control_swap`, `match_case_removal` |
| `other` | residual — logged, never silently absorbed |

Taxonomy v1 left **73.5%** of mutants as `unknown`; v2's gate fails a collection run
above 5%. v1 also declared `statement_deletion` and `return_value_replacement`, which
mutmut cannot generate at all — they were dead one-hot columns.

## Corpus layout

```
corpus/<NN>_<slug>/
    <slug>.py                    # unique, importable, never starts with a digit
    tests_weak/test_<slug>.py    # the suite under evaluation, with recorded gaps
    tests_strong/test_<slug>.py  # the oracle suite
    meta.json                    # tier, omitted_test_cases, provenance, hashes
```

The two suites are in **separate directories** because the harness copies every
`test_*.py` in the directory it is given; a strong suite beside the weak one would be
silently included and destroy the experiment.

Running both yields ground-truth gap labels: survives weak + killed by strong = a real
missing test. See [corpus/GENERATION.md](corpus/GENERATION.md).

## Reproduce from scratch

```bash
cd services/mutation-testing              # under WSL
pip install -r requirements.txt
python scripts/verify_corpus.py           # hashes + both suites green + MANIFEST
python scripts/collect_dataset.py         # → storage/mutants.db, gates enforced
python src/ml/train.py                    # → src/ml/model.json + model_meta.json
```

`model.json` is gitignored — it is a build artifact rebuilt from a versioned corpus.
`model_meta.json` **is** committed: it records the taxonomy version, ordered feature
names, CV metrics, and git SHAs, and `load_model()` rebuilds the one-hot from *it* rather
than from a live constant, so train/inference drift is structurally impossible.

## Known limitations

- **10 corpus modules, not the 15–20 planned.** Every operator family is represented, but
  several by the minimum viable number of modules.
- **3 human-written anchors** (modules 01–03) against 7 AI-generated. Enough to look for
  a gross difference, not to test one.
- **Tier 1 and Tier 2 equivalent-mutant detection are not built.** Only static Tier 0.
- **`match_case_removal` has no training signal** — no corpus module uses `match`.
- **`stable_test_ids` is declared but never read.** The flaky-detection integration point
  exists in the schema only; do not report it as implemented.
- **No benchmarking or ablations yet.** The held-out split is possible now that
  `module_key` exists, but has not been run.

## Design decisions

- [ADR-002: Operator taxonomy v2](../../docs/Architecture/ADR-002-operator-taxonomy-v2.md)
- [ADR-003: Dataset v1 purge and schema versioning](../../docs/Architecture/ADR-003-dataset-schema-versioning.md)
- [Dataset v1 post-mortem](../../docs/Research/experiments/dataset_v1_postmortem.md)
- [mutmut 3.7.0 operator catalogue](../../docs/Research/experiments/mutmut_3.7_operator_catalogue.md)
