import joblib, numpy as np
_model = None
def load(path="/app/models/forecast.bin"):
    global _model; _model = joblib.load(path); return True
def predict_proba(feat: dict) -> float:
    assert _model is not None, "forecast model not loaded"
    cols = ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]
    x = np.array([[feat.get(c,0) for c in cols]])
    return float(_model.predict_proba(x)[0,1])


