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
    if n == 7:
        return ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week", "partial_upload"]
    if n == 6: # New model without size_bytes but with partial_upload? Or just generic fallback
        return ["access_1h","access_24h","recency_s","hour_of_day","day_of_week", "partial_upload"]
    return ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]

def predict_proba(feat: dict) -> float:
    _lazy_load()
    cols = _choose_cols()
    x = np.array([[feat.get(c, 0.0) for c in cols]], dtype=float)
    p = _model.predict_proba(x)[0, 1]
    return float(p)

def recommend_placement(hotness_score: float, endpoints: list) -> dict:
    """
    Recommend the best endpoint based on hotness, cost, and latency.
    endpoints: list of dicts with keys: name, cost_per_gb, latency_ms
    """
    if not endpoints:
        return None

    # Filter valid endpoints
    candidates = endpoints

    # Strategy:
    # Hot (>0.7): Minimize latency.
    # Cold (<0.3): Minimize cost.
    # Warm: Balance (Score = Cost * Latency?) - or just pick "middle" ground.
    
    if hotness_score > 0.7:
        # Sort by latency
        candidates.sort(key=lambda x: x.get("latency_ms", 9999))
        reason = "hot_data_low_latency"
    elif hotness_score < 0.3:
        # Sort by cost
        candidates.sort(key=lambda x: x.get("cost_per_gb", 9999))
        reason = "cold_data_low_cost"
    else:
        # Balanced: minimize (cost * latency)
        # Normalize? Or just simple product
        candidates.sort(key=lambda x: x.get("cost_per_gb", 1) * x.get("latency_ms", 1))
        reason = "warm_data_balanced"

    best = candidates[0]
    return {"endpoint": best["name"], "reason": reason, "hotness": hotness_score}
