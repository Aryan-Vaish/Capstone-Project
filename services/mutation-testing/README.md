# Intelligent Mutation Testing Service

Real mutmut 3.x integration (verified against v3.7.0's actual CLI/output shape,
not assumed from older docs -- mutmut 3.x dropped --paths-to-mutate/--tests-dir
CLI flags in favour of a setup.cfg [mutmut] section, which this wrapper writes
automatically).

## Status (Day 1 of the sprint)
- [x] `mutators/generator.py` -- real mutant generation + execution via mutmut
- [x] `harness/executor.py` -- isolated temp-dir execution per run
- [x] `main.py` -- FastAPI `/mutation/run` endpoint, real (non-mock) output
- [ ] `coverage/` -- coverage-gating (Day 2)
- [ ] `triage/` -- equivalent-mutant filtering (Day 2)
- [ ] `features/`, `ml/` -- feature extraction + XGBoost prioritizer (Day 3)
- [ ] `explainability/` -- SHAP + templated NLG (Day 4)

## Run locally
```bash
pip install -r requirements.txt
cd src && python -m uvicorn main:app --port 8002 --reload
```

## Test
```bash
python -m pytest tests/ -v
```

## Try it
```bash
curl -X POST http://localhost:8002/mutation/run \
  -H "Content-Type: application/json" \
  -d '{"source_file": "corpus/example/module.py", "tests_dir": "corpus/example"}'
```

## Known limitation in the operator classifier
`_guess_operator()` in `mutators/generator.py` pattern-matches mutmut's diff
output against our 7-operator taxonomy. mutmut also generates string-literal
and other mutations outside that taxonomy -- these correctly show up as
`operator: "unknown"` rather than being misclassified. That's expected
behaviour, not a bug (verified against real output on our toy corpus module).
