import argparse, json
import pandas as pd
import numpy as np
import joblib, os

from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score


# --------------------------------------------------------
# TOP-LEVEL DUMMY MODEL (picklable by joblib)
# --------------------------------------------------------
class DummyTierModel:
    """
    Predicts all zeros (no hot files).
    Used when dataset only contains a single class.
    """
    def predict_proba(self, X):
        n = len(X)
        # Column 0 = prob(class=0), Column 1 = prob(class=1)
        return np.column_stack([np.ones(n), np.zeros(n)])

    def predict(self, X):
        return np.zeros(len(X), dtype=int)


def main(args):
    df = pd.read_parquet(args.data)

    X = df[["access_1h","access_24h","size_bytes",
            "recency_s","hour_of_day","day_of_week", "partial_upload"]]

    y = (df["tier"] == "hot").astype(int)

    os.makedirs("/app/models", exist_ok=True)
    os.makedirs("/app/reports", exist_ok=True)

    # --------------------------------------------------------
    # CASE 1: INSUFFICIENT CLASS SAMPLES → use dummy model
    class_counts = y.value_counts().to_dict()
    if y.nunique() < 2 or min(class_counts.values()) < 2:
        clf = DummyTierModel()
        joblib.dump(clf, args.out)

        metrics = {
            "auc_roc": None,
            "auc_pr": None,
            "f1@0.5": None,
            "note": f"Insufficient class samples: {class_counts}. DummyTierModel used."
        }

        with open(args.metrics, "w") as f:
            json.dump(metrics, f, indent=2)

        print("⚠️ Insufficient class samples — saved DummyTierModel.", metrics)
        return

    # --------------------------------------------------------
    # CASE 2: NORMAL TRAINING PATH
    # --------------------------------------------------------
    Xtr, Xte, ytr, yte = train_test_split(
        X, y,
        test_size=0.25,
        random_state=args.seed,
        stratify=y
    )

    clf = RandomForestClassifier(n_estimators=100, class_weight="balanced", random_state=args.seed)
    clf.fit(Xtr, ytr)

    # compute metrics safely
    try:
        p = clf.predict_proba(Xte)[:,1]
        metrics = {
            "auc_roc": float(roc_auc_score(yte, p)),
            "auc_pr": float(average_precision_score(yte, p)),
            "f1@0.5": float(f1_score(yte, (p>=0.5).astype(int))),
        }
    except Exception as e:
        metrics = {
            "auc_roc": None,
            "auc_pr": None,
            "f1@0.5": None,
            "note": f"Metric computation failed: {e}"
        }

    joblib.dump(clf, args.out)

    with open(args.metrics, "w") as f:
        json.dump(metrics, f, indent=2)

    print("Saved trained tier model", metrics)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="/app/models/tier.bin")
    ap.add_argument("--metrics", default="/app/reports/tier_metrics.json")
    ap.add_argument("--seed", type=int, default=42)
    main(ap.parse_args())