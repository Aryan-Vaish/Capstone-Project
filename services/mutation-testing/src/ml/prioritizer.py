"""
ML Prioritizer -- inference side
====================================
Loads a trained XGBoost model (produced by ml/train.py, saved to
ml/model.json) and scores a single mutant's features. If no model file
exists yet, load_model() returns None and main.py gracefully falls back
to ml_priority_score=None for every mutant -- same pattern as the
Day 1/2 TODOs, not a crash.

The one-hot encoding of `operator` here MUST exactly match what
ml/train.py used when training -- OPERATOR_CATEGORIES is shared by both
so they can't drift apart.
"""
from pathlib import Path
from typing import Dict, Optional

MODEL_PATH = Path(__file__).parent / "model.json"

OPERATOR_CATEGORIES = [
    "conditional_boundary", "negate_conditional", "arithmetic_replacement",
    "statement_deletion", "return_value_replacement", "null_check_removal",
    "constant_replacement", "unknown",
]


def features_to_vector(features: Dict) -> list:
    """Turns a feature dict into a fixed-order numeric vector: one-hot
    operator + the numeric/binary features, in a stable order."""
    one_hot = [1 if features["operator"] == op else 0 for op in OPERATOR_CATEGORIES]
    numeric = [
        features["complexity"],
        features["ast_depth"],
        features["in_conditional"],
        features["in_loop"],
        features["in_exception"],
        features["covered"],
    ]
    return one_hot + numeric


def load_model():
    if not MODEL_PATH.exists():
        return None
    import xgboost as xgb
    model = xgb.XGBClassifier()
    model.load_model(str(MODEL_PATH))
    return model


def predict_priority(model, features: Dict) -> Optional[float]:
    """Returns P(this mutant would be killed), per the trained kill-predictor.
    IMPORTANT -- this is only meaningful when called on mutants that we
    already know SURVIVED (see main.py, only called on active_survivors).
    A high score here means: "the model expected this mutant to be killed
    based on its features, but it actually survived" -- i.e. an anomalous,
    surprising gap, which is a stronger signal of a genuine missing test
    than just "this mutant is structurally complex". Low score means the
    model also would have predicted this survives (e.g. it's on rarely-
    exercised code), which is a weaker, less actionable signal.
    """
    if model is None:
        return None
    vector = [features_to_vector(features)]
    proba = model.predict_proba(vector)[0]
    # class 1 = "killed" in training data (see ml/train.py label definition)
    return round(float(proba[1]), 3)
