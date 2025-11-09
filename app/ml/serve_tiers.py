import os, joblib, numpy as np

_model = None
_model_path = os.getenv("TIER_MODEL_PATH", "/app/models/tier.bin")

def set_model_path(path: str):
    global _model_path, _model
    _model_path = path
    _model = None

def load(path: str | None = None) -> bool:
    global _model, _model_path
    if path: _model_path = path
    _model = joblib.load(_model_path)
    return True

def _lazy_load():
    global _model
    if _model is None:
        if os.path.exists(_model_path):
            _model = joblib.load(_model_path)
        else:
            alt = "/app/models/hotness.joblib"
            if os.path.exists(alt):
                _model = joblib.load(alt)
            else:
                raise RuntimeError(f"model not found at '{_model_path}' or '{alt}'")

def _choose_cols():
    n = getattr(_model, "n_features_in_", None)
    if n == 6:
        return ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]
    if n == 5:
        return ["access_1h","access_24h","recency_s","hour_of_day","day_of_week"]
    return ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]

def predict_proba(feat: dict) -> float:
    _lazy_load()
    cols = _choose_cols()
    x = np.array([[feat.get(c, 0.0) for c in cols]], dtype=float)
    p = _model.predict_proba(x)[0, 1]
    return float(p)
