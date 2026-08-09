"""
SHAP Attribution
==================
Explains WHY the ML prioritizer scored a given mutant the way it did --
which features drove the prediction. This is for the report/defense, NOT
the user-facing explanation (see test_gap.py for that) -- SHAP values are
about the MODEL's reasoning, not about the mutant's actual semantics.

Uses TreeExplainer specifically because it's exact and fast for tree
ensembles (XGBoost) -- this was the deciding factor in choosing XGBoost
over a neural net for the prioritizer in the first place (see ADR
discussion / Day 3 notes).
"""
from typing import Dict, List, Tuple

from ml.prioritizer import features_to_vector, OPERATOR_CATEGORIES

FEATURE_NAMES = OPERATOR_CATEGORIES + [
    "complexity", "ast_depth", "in_conditional", "in_loop", "in_exception", "covered",
]


def explain_prediction(model, features: Dict) -> List[Tuple[str, float]]:
    """
    Returns [(feature_name, shap_value), ...] sorted by absolute contribution,
    highest first. Empty list if model is None or SHAP isn't available.
    """
    if model is None:
        return []
    import shap
    explainer = shap.TreeExplainer(model)
    vector = [features_to_vector(features)]
    shap_values = explainer.shap_values(vector)

    # xgboost binary classifier: shap_values shape is (1, n_features) for
    # the positive class in recent shap versions -- handle both shapes
    # defensively since this varies across shap/xgboost version pairs.
    values = shap_values[0] if hasattr(shap_values, "__len__") else shap_values
    if hasattr(values, "shape") and len(values.shape) > 1:
        values = values[0]

    pairs = list(zip(FEATURE_NAMES, values))
    pairs.sort(key=lambda p: abs(p[1]), reverse=True)
    return pairs


def top_contributing_feature(model, features: Dict) -> str:
    """Convenience wrapper: just the single most influential feature name.
    Falls back to 'unknown' if no model or SHAP fails for any reason --
    NEVER crashes the main pipeline over an explainability failure."""
    try:
        pairs = explain_prediction(model, features)
        return pairs[0][0] if pairs else "unknown"
    except Exception:
        return "unknown"
