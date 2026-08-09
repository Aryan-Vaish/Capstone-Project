"""
ML Prioritizer -- training side
====================================
Standalone script (NOT called automatically by main.py) -- run this
manually after you've accumulated enough mutant records in storage/mutants.db
by running /mutation/run against several corpus modules.

LABEL DEFINITION (important, explain this in your report):
  label = 1 if the mutant was KILLED, 0 if it survived (in any bucket --
  uncovered, equivalent_suspected, or active_survivor all count as 0).
  This trains a "kill predictor". At inference time (prioritizer.py),
  we only ever call predict_priority() on mutants we already KNOW
  survived. A mutant the model expected to be killed (high P(killed))
  but which actually survived is exactly the "anomalous, surprising
  test gap" we want to surface first -- this sidesteps the fact that
  "meaningfulness" has no ground-truth label anywhere in the mutation
  testing literature, by reducing it to a well-defined, learnable proxy.

Usage:
    cd services/mutation-testing
    python src/ml/train.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from storage.db import fetch_all_records
from ml.prioritizer import features_to_vector, MODEL_PATH


def main():
    records = fetch_all_records()
    if len(records) < 20:
        print(f"Only {len(records)} records in storage/mutants.db -- need at least ~20 "
              f"to train anything meaningful. Run /mutation/run against more corpus "
              f"modules first, then retry.")
        return

    import xgboost as xgb
    from sklearn.model_selection import train_test_split

    X, y = [], []
    for r in records:
        features = {
            "operator": r["operator"], "complexity": r["complexity"],
            "ast_depth": r["ast_depth"], "in_conditional": r["in_conditional"],
            "in_loop": r["in_loop"], "in_exception": r["in_exception"],
            "covered": r["covered"],
        }
        X.append(features_to_vector(features))
        y.append(1 if r["outcome"] == "killed" else 0)

    print(f"Training on {len(X)} records ({sum(y)} killed, {len(y)-sum(y)} survived).")

    if len(set(y)) < 2:
        print("ERROR: all records have the same label -- need both killed AND survived "
              "examples to train a classifier. Run the pipeline against more modules.")
        return

    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.25, random_state=42)

    model = xgb.XGBClassifier(n_estimators=100, max_depth=4, eval_metric="logloss")
    model.fit(X_train, y_train)

    train_acc = model.score(X_train, y_train)
    val_acc = model.score(X_val, y_val)
    print(f"Train accuracy: {train_acc:.3f} | Validation accuracy: {val_acc:.3f}")

    model.save_model(str(MODEL_PATH))
    print(f"Model saved to {MODEL_PATH}")


if __name__ == "__main__":
    main()
