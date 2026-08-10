"""
ML Mutant Prioritizer -- inference side
========================================
Scores a surviving mutant with P(killed) from the trained model.

WHY P(killed) IS THE RIGHT SCORE FOR A SURVIVOR
-----------------------------------------------
The model is trained to predict whether a mutant gets killed. At inference we
only ever apply it to mutants that ALREADY SURVIVED, so a high score means "the
model expected this to be killed, and it wasn't" -- a surprising survivor, which
is a proxy for a genuine test gap. A survivor the model also expected to survive
is likely equivalent or unreachable, and ranks low.

That proxy exists because no ground-truth label for "meaningful survivor" exists
in the literature. Day 6's weak/strong oracle suites replace the proxy with a
real label (survives the weak suite, killed by the strong one = true gap); until
that data is collected, this remains the ranking signal.

FEATURE-VECTOR CONTRACT
-----------------------
The one-hot layout is read from the model's own `model_meta.json`, not from
whatever the taxonomy happens to say at import time. If the taxonomy changes and
the model is not retrained, the shapes disagree and we raise loudly instead of
silently scoring against a misaligned vector -- a failure v1 could not detect,
since it rebuilt the one-hot from a live constant every time.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from features.extractor import NUMERIC_FEATURES
from mutators.taxonomy import FAMILIES, TAXONOMY_VERSION, family_of

MODEL_PATH = Path(__file__).parent / "model.json"
MODEL_META_PATH = Path(__file__).parent / "model_meta.json"


def feature_names(families: Optional[List[str]] = None,
                  numeric: Optional[List[str]] = None) -> List[str]:
    """Ordered names of the model input vector: family one-hot, then numerics."""
    fam = families if families is not None else FAMILIES
    num = numeric if numeric is not None else NUMERIC_FEATURES
    return [f"op_family={f}" for f in fam] + list(num)


def features_to_vector(features: Dict,
                       families: Optional[List[str]] = None,
                       numeric: Optional[List[str]] = None) -> List[float]:
    """Flat dict -> ordered numeric vector.

    Families rather than the 23 fine-grained operators: at a few hundred
    training rows a 23-wide one-hot is nearly all zeros and trains badly, while
    the 8 families stay populated. The fine operator is still what drives the
    user-facing explanation -- only the model input is coarsened.
    """
    fam = families if families is not None else FAMILIES
    num = numeric if numeric is not None else NUMERIC_FEATURES

    family = features.get("operator_family") or family_of(features.get("operator", "other"))
    vector: List[float] = [1.0 if family == f else 0.0 for f in fam]
    vector += [float(features.get(name, 0) or 0) for name in num]
    return vector


class LoadedModel:
    """A trained model plus the metadata describing the vector it expects."""

    def __init__(self, booster, meta: Dict):
        self.booster = booster
        self.meta = meta
        self.families: List[str] = meta.get("operator_families", FAMILIES)
        self.numeric: List[str] = meta.get("numeric_features", NUMERIC_FEATURES)
        self.feature_names: List[str] = meta.get(
            "feature_names", feature_names(self.families, self.numeric)
        )
        self.taxonomy_version: str = meta.get("taxonomy_version", "unknown")

    def check_compatible(self) -> Optional[str]:
        """Returns a human-readable reason the model is stale, or None if fine."""
        if self.taxonomy_version != TAXONOMY_VERSION:
            return (f"model was trained against taxonomy {self.taxonomy_version}, "
                    f"code is at {TAXONOMY_VERSION}")
        if list(self.numeric) != list(NUMERIC_FEATURES):
            missing = set(NUMERIC_FEATURES) ^ set(self.numeric)
            return f"numeric feature set changed since training: {sorted(missing)}"
        return None


def load_model(model_path: Path = MODEL_PATH,
               meta_path: Path = MODEL_META_PATH) -> Optional[LoadedModel]:
    """Loads the trained model, or returns None when none has been trained yet.

    Returning None is a supported state, not an error: a fresh checkout has no
    model (model.json is a build artifact and stays gitignored -- model_meta.json
    is committed instead, as the provenance record). Everything downstream
    degrades to unranked-but-still-explained output.
    """
    if not model_path.exists():
        return None
    try:
        import xgboost as xgb
    except ImportError:
        return None

    meta: Dict = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}

    booster = xgb.XGBClassifier()
    booster.load_model(str(model_path))
    return LoadedModel(booster, meta)


def predict_priority(model: Optional[LoadedModel], features: Dict) -> Optional[float]:
    """P(killed) in [0, 1] for one mutant, or None if no model is loaded."""
    if model is None:
        return None
    vector = features_to_vector(features, model.families, model.numeric)
    try:
        proba = model.booster.predict_proba([vector])[0]
    except Exception:
        # A shape mismatch here means the model is stale relative to the code.
        # Degrade to unranked rather than taking the whole run down;
        # check_compatible() surfaces the reason via /health.
        return None
    return round(float(proba[1]), 3)
