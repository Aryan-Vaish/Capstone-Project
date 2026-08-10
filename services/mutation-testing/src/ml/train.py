"""
Mutant Prioritizer -- training
================================
Standalone script; never imported by main.py. Run it explicitly:

    cd services/mutation-testing && python src/ml/train.py

Writes ml/model.json (gitignored build artifact) and ml/model_meta.json
(committed -- it is the provenance record that makes a run reproducible).

WHAT THE LABEL MEANS
--------------------
`y = 1` if the mutant was killed. At inference the model is applied ONLY to
mutants that already survived, so a high score means "the model expected this to
be killed and it wasn't" -- a surprising survivor, used as a proxy for a real
test gap. No ground-truth label for "meaningful survivor" exists in the
literature, which is why a proxy is needed at all. Day 6's paired weak/strong
suites replace it with a real label (survives weak, dies to strong = true gap).

THREE METHODOLOGY FIXES OVER v1
-------------------------------
1. POPULATION. v1 trained on every stored row, including `uncovered` ones. But
   `covered = 0` means the line never executes, which means the mutant cannot be
   killed -- so the label was deterministically readable from a single feature
   and the model banked free accuracy on a tautology. It was also a train/serve
   mismatch: inference only ever sees covered, non-arid survivors. Training now
   uses exactly the inference population.

2. SPLITTING. v1 used `train_test_split(..., random_state=42)` over rows. Two
   independent leaks followed: the same mutant appeared up to 8 times (tests
   wrote to the production DB), so byte-identical rows landed on both sides; and
   `complexity`/`ast_depth` were file-level constants, so they functioned as a
   module ID the model could memorise. Splitting is now grouped by module, and
   scores are cross-validated rather than read off one arbitrary split.

3. METRICS. v1 printed accuracy only. Accuracy is close to meaningless on an
   imbalanced ranking task -- and this IS a ranking task. Precision, recall, F1,
   ROC-AUC, PR-AUC and Brier are reported against three trivial baselines, so a
   reader can see whether the model beats "predict the majority class".

Expect the honest numbers to come in BELOW v1's reported 73.3% validation
accuracy. That figure was measured with duplicate rows spanning the split; a
defensible 0.65 is worth more than an indefensible 0.73.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

SRC = Path(__file__).resolve().parents[1]
SERVICE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
for p in (str(REPO_ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from features.extractor import NUMERIC_FEATURES
from ml.prioritizer import MODEL_META_PATH, MODEL_PATH, feature_names, features_to_vector
from mutators.taxonomy import FAMILIES, TAXONOMY_VERSION
from storage.db import DATASET_SCHEMA_VERSION, fetch_training_population

MIN_ROWS = 40
MIN_GROUPS = 4
REPORTS_DIR = SERVICE_ROOT / "reports"


def _git_sha() -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def build_dataset(rows: List[Dict]) -> Tuple[List[List[float]], List[int], List[str]]:
    """Rows -> (X, y, groups). `groups` is module_key, which is what makes a
    leak-free split possible at all -- v1 had no usable module identity."""
    X, y, groups = [], [], []
    for row in rows:
        X.append(features_to_vector(row, FAMILIES, NUMERIC_FEATURES))
        y.append(1 if row.get("outcome") == "killed" else 0)
        groups.append(row.get("module_key") or "unknown")
    return X, y, groups


def _metrics(y_true, y_pred, y_proba) -> Dict[str, float]:
    from sklearn.metrics import (
        accuracy_score, average_precision_score, brier_score_loss, f1_score,
        precision_score, recall_score, roc_auc_score,
    )
    out = {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
    }
    # AUCs are undefined when a fold holds only one class
    if len(set(y_true)) > 1:
        out["roc_auc"] = roc_auc_score(y_true, y_proba)
        out["pr_auc"] = average_precision_score(y_true, y_proba)
        out["brier"] = brier_score_loss(y_true, y_proba)
    return {k: round(float(v), 4) for k, v in out.items()}


def _baselines(X, y, groups) -> Dict[str, Dict[str, float]]:
    """Trivial reference points. If the model cannot beat these, say so."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    X_arr, y_arr = np.asarray(X, dtype=float), np.asarray(y)
    n_splits = min(5, len(set(groups)))
    gkf = GroupKFold(n_splits=n_splits)

    covered_idx = feature_names(FAMILIES, NUMERIC_FEATURES).index("covered")
    results: Dict[str, Dict[str, float]] = {}

    majority = int(round(y_arr.mean()))
    results["majority_class"] = _metrics(y_arr, [majority] * len(y_arr),
                                         [float(majority)] * len(y_arr))

    covered_pred = (X_arr[:, covered_idx] > 0).astype(int)
    results["covered_only"] = _metrics(y_arr, covered_pred, covered_pred.astype(float))

    lr_true, lr_pred, lr_proba = [], [], []
    for tr, te in gkf.split(X_arr, y_arr, groups):
        if len(set(y_arr[tr])) < 2:
            continue
        lr = LogisticRegression(max_iter=2000, class_weight="balanced")
        lr.fit(X_arr[tr], y_arr[tr])
        lr_true.extend(y_arr[te])
        lr_pred.extend(lr.predict(X_arr[te]))
        lr_proba.extend(lr.predict_proba(X_arr[te])[:, 1])
    if lr_true:
        results["logistic_regression"] = _metrics(lr_true, lr_pred, lr_proba)
    return results


def cross_validate(X, y, groups) -> Tuple[Dict, List[Dict]]:
    """Module-grouped CV. Reports mean +/- std across folds rather than a single
    split: with fewer than ~20 groups, one random group split has enormous
    variance, so a lone number would be noise dressed as a result."""
    import numpy as np
    import xgboost as xgb
    from sklearn.model_selection import GroupKFold

    X_arr, y_arr = np.asarray(X, dtype=float), np.asarray(y)
    n_splits = min(5, len(set(groups)))
    gkf = GroupKFold(n_splits=n_splits)

    fold_metrics: List[Dict] = []
    pooled_true, pooled_pred, pooled_proba = [], [], []

    for i, (tr, te) in enumerate(gkf.split(X_arr, y_arr, groups)):
        if len(set(y_arr[tr])) < 2 or len(set(y_arr[te])) < 2:
            continue
        pos, neg = int(y_arr[tr].sum()), int((1 - y_arr[tr]).sum())
        model = xgb.XGBClassifier(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            subsample=0.9, colsample_bytree=0.9,
            scale_pos_weight=(neg / pos) if pos else 1.0,
            eval_metric="logloss", random_state=42,
        )
        model.fit(X_arr[tr], y_arr[tr])
        proba = model.predict_proba(X_arr[te])[:, 1]
        pred = (proba >= 0.5).astype(int)

        m = _metrics(y_arr[te], pred, proba)
        m["fold"] = i
        m["n_test"] = int(len(te))
        m["test_modules"] = sorted({groups[j] for j in te})
        fold_metrics.append(m)

        pooled_true.extend(y_arr[te])
        pooled_pred.extend(pred)
        pooled_proba.extend(proba)

    summary: Dict[str, Dict[str, float]] = {}
    if fold_metrics:
        keys = [k for k in fold_metrics[0] if isinstance(fold_metrics[0][k], float)]
        for k in keys:
            vals = [f[k] for f in fold_metrics if k in f]
            summary[k] = {
                "mean": round(float(np.mean(vals)), 4),
                "std": round(float(np.std(vals)), 4),
            }
    pooled = _metrics(pooled_true, pooled_pred, pooled_proba) if pooled_true else {}
    return {"per_fold_summary": summary, "pooled": pooled}, fold_metrics


def main() -> int:
    rows = fetch_training_population()
    n_groups = len({r.get("module_key") for r in rows})

    print(f"training population: {len(rows)} rows across {n_groups} modules "
          f"(covered, non-arid, weak-suite survivors+kills only)")

    if len(rows) < MIN_ROWS:
        print(f"REFUSING TO TRAIN: need >= {MIN_ROWS} rows, have {len(rows)}.\n"
              f"Collect more corpus modules first (scripts/collect_dataset.py).")
        return 1
    if n_groups < MIN_GROUPS:
        print(f"REFUSING TO TRAIN: need >= {MIN_GROUPS} distinct modules for a "
              f"leak-free grouped split, have {n_groups}.\n"
              f"A model trained on fewer is not evaluable and must not be reported.")
        return 1

    X, y, groups = build_dataset(rows)
    if len(set(y)) < 2:
        print("REFUSING TO TRAIN: only one outcome class present.")
        return 1

    print(f"class balance: {sum(y)} killed / {len(y) - sum(y)} survived")

    cv_results, fold_metrics = cross_validate(X, y, groups)
    baselines = _baselines(X, y, groups)

    print("\n--- grouped cross-validation (mean +/- std across folds) ---")
    for name, stat in cv_results["per_fold_summary"].items():
        print(f"  {name:<12} {stat['mean']:.4f} +/- {stat['std']:.4f}")
    print("\n--- baselines ---")
    for name, m in baselines.items():
        print(f"  {name:<22} f1={m.get('f1', 0):.3f}  pr_auc={m.get('pr_auc', float('nan')):.3f}")

    # Final model on all available data, for serving.
    import numpy as np
    import xgboost as xgb
    X_arr, y_arr = np.asarray(X, dtype=float), np.asarray(y)
    pos, neg = int(y_arr.sum()), int((1 - y_arr).sum())
    final = xgb.XGBClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        subsample=0.9, colsample_bytree=0.9,
        scale_pos_weight=(neg / pos) if pos else 1.0,
        eval_metric="logloss", random_state=42,
    )
    final.fit(X_arr, y_arr)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    final.save_model(str(MODEL_PATH))

    meta = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "taxonomy_version": TAXONOMY_VERSION,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "pipeline_git_sha": _git_sha(),
        "operator_families": FAMILIES,
        "numeric_features": NUMERIC_FEATURES,
        "feature_names": feature_names(FAMILIES, NUMERIC_FEATURES),
        "n_rows": len(rows),
        "n_groups": n_groups,
        "class_balance": {"killed": int(sum(y)), "survived": int(len(y) - sum(y))},
        "training_population": (
            "covered = 1 AND outcome != 'uncovered' AND "
            "triage_status != 'equivalent_suspected' AND suite = 'weak'"
        ),
        "cv": cv_results,
        "cv_folds": fold_metrics,
        "baselines": baselines,
        "hyperparameters": {
            "n_estimators": 200, "max_depth": 3, "learning_rate": 0.05,
            "subsample": 0.9, "colsample_bytree": 0.9,
            "scale_pos_weight": (neg / pos) if pos else 1.0,
        },
    }
    MODEL_META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    (REPORTS_DIR / f"model_evaluation_{stamp}.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    print(f"\nwrote {MODEL_PATH}")
    print(f"wrote {MODEL_META_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
