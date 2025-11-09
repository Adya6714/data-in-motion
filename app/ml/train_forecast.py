import argparse, json
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score, average_precision_score, f1_score

def main(args):
    df = pd.read_parquet(args.data)
    df = df[df["tier"]!="hot"].copy()
    X = df[["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]]
    y = df["y_hot_soon"].astype(int)
    if y.sum()==0:  
        print("No positive hot_soon labels; training degenerate model.")
    Xtr,Xte,ytr,yte = train_test_split(X,y,test_size=0.25,random_state=args.seed,stratify=y if y.sum()>0 else None)

    base = LogisticRegression(max_iter=1000, class_weight="balanced")
    clf = CalibratedClassifierCV(base, cv=3, method="isotonic")
    clf.fit(Xtr,ytr)
    p = clf.predict_proba(Xte)[:,1] if len(Xte)>0 else [0.0]

    metrics = {
        "auc_roc": float(roc_auc_score(yte,p)) if len(Xte)>0 else None,
        "auc_pr": float(average_precision_score(yte,p)) if len(Xte)>0 else None,
        "f1@0.5": float(f1_score(yte,(p>=0.5).astype(int))) if len(Xte)>0 else None,
    }
    import joblib, os
    os.makedirs("/app/models", exist_ok=True)
    joblib.dump(clf, args.out)
    with open(args.metrics,"w") as f: json.dump(metrics,f,indent=2)
    print("Saved", args.out, "metrics", metrics)

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", default="/app/models/forecast.bin")
    ap.add_argument("--metrics", default="/app/reports/forecast_metrics.json")
    ap.add_argument("--seed", type=int, default=42)
    main(ap.parse_args())


