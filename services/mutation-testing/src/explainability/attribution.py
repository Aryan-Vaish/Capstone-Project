"""
SHAP Attribution -- explaining the MODEL, not the code
=======================================================
Answers "why did the ranker score this mutant highly?" using SHAP's
TreeExplainer, which is exact (not approximate) on tree ensembles -- the reason
the prioritizer is a gradient-boosted tree rather than anything else.

Kept strictly separate from explainability/test_gap.py, which answers the
different and more useful question "what test is missing?". SHAP output belongs
in the per-run report and the write-up, where the audience is evaluating the
model; it does not belong in the user-facing Evidence Object, where the audience
just wants to know what to go and write.

v1 defined this module and then never imported it anywhere -- SHAP values
reached no report, no database, and no reader. It is now called from main.py per
scored survivor and written into reports/run_<run_id>.json.
"""
from typing import Dict, List, Optional, Tuple

from ml.prioritizer import LoadedModel, features_to_vector


def explain_prediction(model: Optional[LoadedModel], features: Dict,
                       top_n: Optional[int] = None) -> List[Tuple[str, float]]:
    """(feature_name, shap_value) pairs, largest absolute contribution first.

    Returns [] rather than raising when SHAP is unavailable or the model is
    missing: attribution is diagnostic, and losing it must never fail a run that
    is otherwise complete.
    """
    if model is None:
        return []
    try:
        import shap
    except ImportError:
        return []

    vector = features_to_vector(features, model.families, model.numeric)
    try:
        explainer = shap.TreeExplainer(model.booster)
        raw = explainer.shap_values([vector])
    except Exception:
        return []

    # shap returns different shapes depending on version and objective:
    # (n, features), (n, features, classes), or a list per class.
    values = raw
    while isinstance(values, list):
        values = values[-1]
    try:
        row = values[0]
        # a trailing class axis on binary objectives
        if hasattr(row, "__len__") and len(row) and hasattr(row[0], "__len__"):
            row = [v[-1] for v in row]
    except (IndexError, TypeError):
        return []

    names = model.feature_names
    pairs = [(names[i] if i < len(names) else f"f{i}", float(v))
             for i, v in enumerate(row)]
    pairs.sort(key=lambda p: abs(p[1]), reverse=True)
    return pairs[:top_n] if top_n else pairs


def top_contributing_feature(model: Optional[LoadedModel], features: Dict) -> str:
    """Name of the single feature that most drove this mutant's score."""
    pairs = explain_prediction(model, features, top_n=1)
    return pairs[0][0] if pairs else "unavailable"
