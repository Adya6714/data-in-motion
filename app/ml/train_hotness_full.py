import argparse, os, sqlite3, pandas as pd, numpy as np
from pathlib import Path
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.pipeline import Pipeline
from sklearn.metrics import classification_report, average_precision_score
import joblib

def load_events(conn, only_action="read"):
    q = "SELECT key, ts FROM access_event" + ("" if not only_action else " WHERE action=?")
    df = pd.read_sql_query(q, conn, params=(only_action,) if only_action else None, parse_dates=["ts"])
    if df.empty:
        raise SystemExit("No access_event rows found. Generate traffic first.")
    return df

def featurize_per_key(g, future_win_min: int, future_min_hits: int):
    g = g.set_index("ts").sort_index()
    per_min = g.resample("1min").size().rename("hit").to_frame()

    per_min["access_1h"]  = per_min["hit"].rolling(60,  min_periods=1).sum()
    per_min["access_24h"] = per_min["hit"].rolling(1440, min_periods=1).sum()

    last_hit = (per_min["hit"] > 0).astype(int)
    mins_since = (~(last_hit.astype(bool))).astype(int)
    mins_since = mins_since.groupby((last_hit==1).cumsum()).cumcount()
    per_min["recency_s"] = mins_since * 60.0

    per_min["hour_of_day"] = per_min.index.hour
    per_min["day_of_week"] = per_min.index.dayofweek

    future_hits = per_min["hit"].rolling(future_win_min, min_periods=1).sum().shift(-future_win_min).fillna(0)
    per_min["y_hot_soon"] = (future_hits >= future_min_hits).astype(int)
    return per_min.reset_index()

def build_dataset(db_path: str, future_win_min: int, future_min_hits: int):
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    ev = load_events(conn)
    conn.close()

    feats = []
    for key, g in ev.groupby("key"):
        fg = featurize_per_key(g, future_win_min, future_min_hits)
        fg["key"] = key
        feats.append(fg)
    feats = pd.concat(feats, ignore_index=True)

    X = feats[["access_1h","access_24h","recency_s","hour_of_day","day_of_week"]].astype(float)
    y = feats["y_hot_soon"].astype(int)

    # If there are no positives, relax the rule to get a usable dataset
    if y.sum() == 0 and len(y) > 0:
        future_min_hits = max(1, future_min_hits // 2)
        feats = []
        ev = ev.rename(columns={"key":"key","ts":"ts"})
        for key, g in ev.groupby("key"):
            fg = featurize_per_key(g, future_win_min, future_min_hits)
            fg["key"] = key
            feats.append(fg)
        feats = pd.concat(feats, ignore_index=True)
        X = feats[["access_1h","access_24h","recency_s","hour_of_day","day_of_week"]].astype(float)
        y = feats["y_hot_soon"].astype(int)

    return X, y

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/app/data/state.db")
    ap.add_argument("--out", default="/app/models/tier.bin")
    ap.add_argument("--metrics", default="/app/reports/tier_metrics.json")
    ap.add_argument("--future_win_min", type=int, default=60)
    ap.add_argument("--future_min_hits", type=int, default=10)
    args = ap.parse_args()

    X, y = build_dataset(args.db, args.future_win_min, args.future_min_hits)
    if len(np.unique(y)) < 2:
        print("Warning: labels are not mixed (all 0 or all 1). Training a degenerate model.")
        strat = None
    else:
        strat = y

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, random_state=42, stratify=strat)

    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", CalibratedClassifierCV(LogisticRegression(max_iter=1000, class_weight="balanced"), cv=3, method="isotonic"))
    ])
    pipe.fit(Xtr, ytr)

    ypro = pipe.predict_proba(Xte)[:,1] if len(Xte) else np.array([0.0])
    ap_score = float(average_precision_score(yte, ypro)) if len(Xte) else None
    print(classification_report(yte, (ypro>=0.5).astype(int)) if len(Xte) else "No test split.")
    print("PR-AUC:", ap_score)

    Path(os.path.dirname(args.out)).mkdir(parents=True, exist_ok=True)
    Path(os.path.dirname(args.metrics)).mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, args.out)
    import json
    with open(args.metrics,"w") as f:
        json.dump({"pr_auc": ap_score, "n_samples": int(len(y))}, f, indent=2)
    print("Saved model to", args.out)

if __name__ == "__main__":
    main()
