import argparse, json, time, hashlib
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from sqlalchemy import create_engine

ENGINE = create_engine("sqlite:////app/data/state.db")

def load_snapshot(now_ts_ms: int):
    df = pd.read_sql("SELECT key, access_1h, access_24h, size_bytes, content_type, last_access_ts FROM file_meta", ENGINE)
    df["now_ms"] = now_ts_ms
    df["hour_of_day"] = (now_ts_ms//1000//3600) % 24
    df["day_of_week"] = (now_ts_ms//1000//86400 + 4) % 7
    if 'last_access_ts' in df.columns and df['last_access_ts'].notna().any():
        df['last_access_ms'] = pd.to_datetime(df['last_access_ts']).astype('int64') // 1_000_000
    else:
        df['last_access_ms'] = now_ts_ms
    df["recency_s"] = (now_ts_ms - df["last_access_ms"]).fillna(now_ts_ms).clip(lower=0) / 1000.0
    df.fillna({"access_1h":0,"access_24h":0,"size_bytes":0,"content_type":"bin"}, inplace=True)
    return df

def select_thresholds(df, mode, hot1h, hot24h, warm1h, warm24h, qhot, qwarm):
    if mode == "fixed":
        return dict(hot1h=hot1h, hot24h=hot24h, warm1h=warm1h, warm24h=warm24h)
    q = df[["access_1h","access_24h"]].quantile([qhot, qwarm])
    th = dict(
        hot1h = max(int(q.loc[qhot,"access_1h"]), 5),
        hot24h= max(int(q.loc[qhot,"access_24h"]), 20),
        warm1h= max(int(q.loc[qwarm,"access_1h"]), 2),
        warm24h= max(int(q.loc[qwarm,"access_24h"]), 5),
    )
    return th

def tier(c1h,c24h, th):
    if c1h>=th["hot1h"] or c24h>=th["hot24h"]: return "hot"
    if c1h>=th["warm1h"]or c24h>=th["warm24h"]: return "warm"
    return "cold"

def main(args):
    now_ms = int(time.time()*1000)
    df_now = load_snapshot(now_ms)
    th = select_thresholds(df_now, args.label_mode, args.hot_1h, args.hot_24h, args.warm_1h, args.warm_24h, args.q_hot, args.q_warm)
    df_now["tier"] = [tier(r.access_1h, r.access_24h, th) for r in df_now.itertuples()]

    Hmin = args.horizon_minutes
    df_future = df_now.copy()
    df_future["access_1h"]   = (df_future["access_1h"]*0.9 + 3).round().astype(int)
    df_future["access_24h"]  = (df_future["access_24h"]*0.95 + 10).round().astype(int)
    fut_tier = [tier(r.access_1h, r.access_24h, th) for r in df_future.itertuples()]
    df_now["y_hot_soon"] = ((df_now["tier"]!="hot") & (pd.Series(fut_tier)=="hot")).astype(int)

    feats = ["access_1h","access_24h","size_bytes","recency_s","hour_of_day","day_of_week"]
    out = df_now[["key"]+feats+["tier","y_hot_soon"]].copy()
    out.to_parquet(args.out, index=False)

    meta = {
        "created_at": datetime.utcnow().isoformat()+"Z",
        "rows": int(out.shape[0]),
        "thresholds": th,
        "label_mode": args.label_mode,
        "horizon_minutes": Hmin,
        "sha256": hashlib.sha256(out.to_parquet(index=False)).hexdigest()
    }
    with open(args.out+".meta.json","w") as f: json.dump(meta,f,indent=2)
    print("Wrote", args.out, "rows=", out.shape[0], "thresholds=", th)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--label-mode", choices=["fixed","quantile"], default="fixed")
    ap.add_argument("--hot-1h", type=int, default=50)
    ap.add_argument("--hot-24h", type=int, default=300)
    ap.add_argument("--warm-1h", type=int, default=10)
    ap.add_argument("--warm-24h", type=int, default=60)
    ap.add_argument("--q-hot", type=float, default=0.9)
    ap.add_argument("--q-warm", type=float, default=0.6)
    ap.add_argument("--horizon-minutes", type=int, default=20)
    args = ap.parse_args()
    main(args)


