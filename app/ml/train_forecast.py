import argparse, json, os
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, roc_auc_score, average_precision_score
from sklearn.model_selection import train_test_split
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
import joblib
from collections import Counter

def safe_predict_proba(clf, X):
    """Return probability of class=1 even if model has only one class."""
    if hasattr(clf, "classes_"):
        classes = list(clf.classes_)
        if len(classes) == 1:
            return np.ones(len(X)) if classes[0] == 1 else np.zeros(len(X))
        else:
            pos_idx = classes.index(1)
            return clf.predict_proba(X)[:, pos_idx]
    return np.zeros(len(X))

def main(args):
    df = pd.read_parquet(args.data)
    df = df[df["tier"] != "hot"].copy()

    if "y_hot_soon" not in df.columns:
        raise ValueError("Column y_hot_soon missing from training dataset")

    X = df[["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]]
    y = df["y_hot_soon"].astype(int)

    class_counts = Counter(y.tolist())
    print("Class counts:", class_counts)

    # ===================== CASE 1: NO POSITIVE LABELS ============================
    if y.sum() == 0 or len(class_counts) < 2:
        print("No positive hot_soon labels OR only one class — using DummyClassifier.")
        clf = DummyClassifier(strategy="most_frequent")
        clf.fit(X, y)

        p = safe_predict_proba(clf, X)
        metrics = {
            "note": "dummy model used (no positive samples or one class)",
            "pos_fraction": float(y.mean())
        }

    else:
        # ===================== NORMAL TRAINING PATH ==============================
        Xtr, Xte, ytr, yte = train_test_split(
            X, y, test_size=0.25, random_state=args.seed, stratify=y
        )

        base = LogisticRegression(max_iter=1000, class_weight="balanced")

        train_counts = Counter(ytr.tolist())
        min_per_class = min(train_counts.values())

        if min_per_class < 3:
            print("Too few samples per class — training uncalibrated LogisticRegression.")
            clf = base
            clf.fit(Xtr, ytr)
        else:
            cv_folds = min(3, min_per_class)
            print(f"Training with {cv_folds}-fold calibrated CV.")
            clf = CalibratedClassifierCV(base, cv=cv_folds, method="isotonic")
            clf.fit(Xtr, ytr)

        X_eval = Xte if len(Xte) > 0 else Xtr
        y_eval = yte if len(Xte) > 0 else ytr

        p = safe_predict_proba(clf, X_eval)

        try:
            auc_roc = float(roc_auc_score(y_eval, p))
        except:
            auc_roc = None

        try:
            auc_pr = float(average_precision_score(y_eval, p))
        except:
            auc_pr = None

        try:
            f1 = float(f1_score(y_eval, (p >= 0.5).astype(int)))
        except:
            f1 = None

        metrics = {"auc_roc": auc_roc, "auc_pr": auc_pr, "f1@0.5": f1}

    os.makedirs("/app/models", exist_ok=True)
    joblib.dump(clf, args.out)

    os.makedirs("/app/reports", exist_ok=True)
    with open(args.metrics, "w") as f:
        json.dump(metrics, f, indent=2)

    print("Saved:", args.out)
    print("Metrics:", metrics)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="/app/models/forecast.bin")
    ap.add_argument("--metrics", default="/app/reports/forecast_metrics.json")
    ap.add_argument("--seed", type=int, default=42)
    main(ap.parse_args())
