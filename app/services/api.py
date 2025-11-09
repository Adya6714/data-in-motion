from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone
from sqlalchemy import text
from .optimizer.service import evaluate_and_queue
import asyncio


from .common.db import init_db, SessionLocal, FileMeta, MigrationTask
from .optimizer.service import evaluate_and_queue
from .migrator.service import process_queue_once
from .migrator import tools as migrator_tools
from .api_ml import r as ml_router
from .optimizer.placement_milp import Site, solve_placement
from .optimizer.scoring import score_location, Weights
from app.ml import serve_tiers
from .common.config import (
    S3_ENDPOINTS,
    SLA_LATENCY_MS,
    REPLICATION_FACTOR,
    HEAT_WARM_THRESHOLD,
    HEAT_HOT_THRESHOLD,
    HEAT_PRED_BOOST,
)
from .policy import security, chaos
from .observability import alerts
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from .observability.metrics import simulate_events_total
from .optimizer.model import optimize_placement, predict_hot_prob, get_candidate_sites

app = FastAPI(title="Data-in-Motion API")

@app.on_event("startup")
def _startup(): 
    init_db()
    asyncio.create_task(_auto_placement_loop())

async def _auto_placement_loop():
    while True:
        try:
            with SessionLocal() as s:
                rows = s.execute(text(
                    "SELECT key FROM file_meta ORDER BY last_access_ts DESC"
                )).fetchall()
                keys = [r[0] for r in rows]
            for k in keys:
                evaluate_and_queue(k)
            process_queue_once()
        except Exception:
            pass
        await asyncio.sleep(5)

app.include_router(ml_router)

class FileOut(BaseModel):
    key: str; tier: str; primary: str; replicas: List[str]; access_1h: int; access_24h: int; version_token: str | None = None
    @classmethod
    def from_meta(cls, m: FileMeta):
        reps = [p for p in (m.location_replicas or "").split(",") if p]
        return cls(
            key=m.key,
            tier=m.tier,
            primary=m.location_primary,
            replicas=reps,
            access_1h=m.access_1h,
            access_24h=m.access_24h,
            version_token=getattr(m, "version_token", None),
        )

class SecurityPolicy(BaseModel):
    enforce: bool

class ToolRequest(BaseModel):
    src: str
    dst: str
    prefix: str | None = None

@app.get("/files", response_model=List[FileOut])
def list_files():
    with SessionLocal() as s:
        return [FileOut.from_meta(m) for m in s.query(FileMeta).all()]

@app.get("/policy/security")
def get_security_policy():
    return {"enforce": security.is_encryption_enforced()}

@app.post("/policy/security", response_model=SecurityPolicy)
def set_security_policy(body: SecurityPolicy):
    security.set_enforcement(body.enforce)
    return body

@app.get("/endpoints")
def list_endpoints():
    return [e.model_dump() for e in S3_ENDPOINTS]

@app.post("/optimize/{key:path}")
def optimize_now(key: str):
    if key.strip().lower() == "all":
        return optimize_all()
    evaluate_and_queue(key)
    return {"status": "queued_if_needed"}

@app.post("/simulate")
def simulate(key: str, events: int = Query(100, ge=1, le=10000)):
    now_dt = datetime.now(timezone.utc)
    with SessionLocal() as s:
        fm = s.query(FileMeta).filter_by(key=key).first()
        if not fm:
            fm = FileMeta(
                key=key,
                size_bytes=0,
                content_type="",     
                checksum="",           
                tier="cold",
                location_primary="aws",
                location_replicas="",
                last_access_ts=now_dt,  
                access_1h=0,
                access_24h=0,
            )
            s.add(fm)
            s.flush()

        fm.access_1h += events
        fm.access_24h += events
        fm.last_access_ts = now_dt

        rows = [{"k": key, "ts": now_dt, "act": "read"} for _ in range(events)]
        s.execute(
            text("INSERT INTO access_event(key, ts, action) VALUES (:k, :ts, :act)"),
            rows,
        )

        s.commit()
    
    evaluate_and_queue(key)
    simulate_events_total.inc(events)

    return {"ok": True, "incremented": events}

@app.post("/optimize/all")
def optimize_all():
    with SessionLocal() as s:
        keys = [m.key for m in s.query(FileMeta).all()]
        before_tasks = s.execute(text("SELECT COUNT(1) FROM migration_task")).scalar() or 0
    for k in keys:
        try:
            evaluate_and_queue(k)
        except Exception:
            pass
    with SessionLocal() as s:
        after_tasks = s.execute(text("SELECT COUNT(1) FROM migration_task")).scalar() or 0
    created = max(0, int(after_tasks) - int(before_tasks))
    return {"total": len(keys), "updated": created}

@app.get("/explain/{key:path}")
def explain(key: str):
    sites = [Site(name=e.name, p95_ms=e.latency_ms, cost_gb=e.cost_per_gb, provider=e.name, region=e.name) for e in S3_ENDPOINTS]
    with SessionLocal() as s:
        fm = s.query(FileMeta).filter_by(key=key).first()
    cost_vals = [float(s.cost_gb) for s in sites]
    lat_vals = [float(s.p95_ms) for s in sites]
    feat = {
        "sla_ms": SLA_LATENCY_MS,
        "affinity_regions": [],
        "egress_penalty": {s.name: (0.0 if (fm and fm.location_primary == s.name) else 1.0) for s in sites},
        "cost_min": min(cost_vals) if cost_vals else None,
        "cost_max": max(cost_vals) if cost_vals else None,
        "lat_min": min(lat_vals) if lat_vals else None,
        "lat_max": max(lat_vals) if lat_vals else None,
    }
    try:
        if getattr(serve_tiers, "_model", None) is not None and fm is not None:
            feat["p_hot"] = serve_tiers.predict_proba({
                "access_1h": fm.access_1h,
                "access_24h": fm.access_24h,
                "size_bytes": fm.size_bytes,
                "recency_s": 0.0,
                "hour_of_day": 0,
                "day_of_week": 0,
            })
        else:
            feat["p_hot"] = 1.0 if (fm and fm.tier=="hot") else 0.0
    except Exception:
        feat["p_hot"] = 1.0 if (fm and fm.tier=="hot") else 0.0

    w = Weights()
    scores = [score_location(feat, s, w) for s in sites]
    chosen, explain = solve_placement(
        sites,
        rf=REPLICATION_FACTOR,
        sla_ms=SLA_LATENCY_MS,
        site_scores=scores,
        score_weight=0.2,
    )
    explain["scores"] = {sites[i].name: scores[i] for i in range(len(sites))}
    explain["p_hot"] = feat["p_hot"]
    return explain

@app.get("/debug/placement/{key:path}")
def debug_placement(key: str, run: bool = False):
    """
    Inspect the inputs used for placement and optionally run evaluate_and_queue.
    Returns current DB values, computed windows, prediction, and recommended placement.
    """
    with SessionLocal() as s:
        fm = s.query(FileMeta).filter_by(key=key).first()
        # Compute 1h/24h windows similarly to service.py (SQLite-friendly path)
        try:
            rows = s.execute(text(
                """
                SELECT
                  SUM(CASE WHEN ts >= datetime('now','-1 hour') THEN 1 ELSE 0 END) AS c1,
                  SUM(CASE WHEN ts >= datetime('now','-24 hour') THEN 1 ELSE 0 END) AS c24,
                  MAX(ts) AS last_ts
                FROM access_event
                WHERE key = :k
                """
            ), {"k": key}).first()
            c1 = int(rows.c1 or 0)
            c24 = int(rows.c24 or 0)
            last_ts = rows.last_ts
        except Exception:
            c1 = fm.access_1h if fm else 0
            c24 = fm.access_24h if fm else 0
            last_ts = getattr(fm, "last_access_ts", None) if fm else None

    # Prediction and adjusted probability
    size_bytes = int(fm.size_bytes) if fm else 0
    heat_score = float(getattr(fm, "heat_score", 0.0) or 0.0) if fm else 0.0
    p = predict_hot_prob(c1, c24, size_bytes)
    sites = get_candidate_sites()
    w = Weights()
    cost_vals = [float(s.cost_gb) for s in sites]
    lat_vals = [float(s.p95_ms) for s in sites]
    feat_ctx = {
        "p_hot": p,
        "sla_ms": SLA_LATENCY_MS,
        "affinity_regions": [],
        "cost_min": min(cost_vals) if cost_vals else None,
        "cost_max": max(cost_vals) if cost_vals else None,
        "lat_min": min(lat_vals) if lat_vals else None,
        "lat_max": max(lat_vals) if lat_vals else None,
        "egress_penalty": {s.name: (0.0 if (fm and fm.location_primary == s.name) else 1.0) for s in sites},
    }
    scores = [score_location(feat_ctx, s, w) for s in sites]
    max_score = max(scores) if scores else 0.0
    site_quality_factor = max(0.6, min(1.0, max_score / 3.0))
    p_adj = p * (0.6 + 0.4 * site_quality_factor)

    # Recommended placement (dry-run via optimize_placement)
    placement = optimize_placement(key, size_bytes, c1, c24)
    heat_snapshot = {
        "score": heat_score,
        "boosted": heat_score + HEAT_PRED_BOOST * max(0.0, min(1.0, p)),
        "warm_threshold": HEAT_WARM_THRESHOLD,
        "hot_threshold": HEAT_HOT_THRESHOLD,
    }

    out = {
        "key": key,
        "db_before": {
            "tier": getattr(fm, "tier", None) if fm else None,
            "primary": getattr(fm, "location_primary", None) if fm else None,
            "replicas": (getattr(fm, "location_replicas", "") or "").split(",") if fm else [],
            "access_1h": getattr(fm, "access_1h", 0) if fm else 0,
            "access_24h": getattr(fm, "access_24h", 0) if fm else 0,
            "last_access_ts": getattr(fm, "last_access_ts", None) if fm else None,
            "heat_score": heat_score,
        },
        "windows": {"c1": c1, "c24": c24, "last_ts": str(last_ts) if last_ts else None},
        "prediction": {"p": p, "p_adj": p_adj, "scores": {sites[i].name: scores[i] for i in range(len(sites))}},
        "heat": heat_snapshot,
        "recommended": {"tier": placement.tier, "primary": placement.primary, "replicas": placement.replicas, "heat_score": heat_score},
    }

    if run:
        evaluate_and_queue(key)
        with SessionLocal() as s:
            fm2 = s.query(FileMeta).filter_by(key=key).first()
            out["db_after"] = {
                "tier": getattr(fm2, "tier", None) if fm2 else None,
                "primary": getattr(fm2, "location_primary", None) if fm2 else None,
                "replicas": (getattr(fm2, "location_replicas", "") or "").split(",") if fm2 else [],
                "access_1h": getattr(fm2, "access_1h", 0) if fm2 else 0,
                "access_24h": getattr(fm2, "access_24h", 0) if fm2 else 0,
                "last_access_ts": getattr(fm2, "last_access_ts", None) if fm2 else None,
            }
    return out

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/migrator/tick")
def migrator_tick():
    return {"processed": process_queue_once()}

@app.get("/tasks")
def tasks():
    with SessionLocal() as s:
        xs = s.query(MigrationTask).all()
        return [{"id":t.id,"key":t.key,"src":t.src,"dst":t.dst,"status":t.status,"error":t.error} for t in xs]

@app.delete("/tasks")
def clear_tasks(status: str = "all"):
    valid = {"queued", "running", "done", "failed", "cleanup"}
    with SessionLocal() as s:
        q = s.query(MigrationTask)
        if status != "all":
            if status not in valid:
                return {"deleted": 0, "error": f"invalid status '{status}'"}
            q = q.filter_by(status=status)
        deleted = q.delete(synchronize_session=False)
        s.commit()
    return {"deleted": deleted}

@app.get("/debug/db")
def debug_db():
    out = {"tables": [], "counts": {}}
    with SessionLocal() as s:
        try:
            rows = s.execute(text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")).fetchall()
            tables = [r[0] for r in rows]
            out["tables"] = tables
            for t in tables:
                try:
                    c = s.execute(text(f"SELECT COUNT(1) FROM {t}")).scalar()
                    out["counts"][t] = int(c or 0)
                except Exception as e:
                    out["counts"][t] = f"err: {e}"
        except Exception as e:
            out["error"] = str(e)
    return out

@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/alerts")
def get_alerts(include_ack: bool = False):
    return alerts.list_alerts(include_ack=include_ack)

@app.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    if not alerts.acknowledge(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"acknowledged": True, "id": alert_id}

@app.post("/alerts/clear")
def clear_alerts():
    alerts.clear_alerts()
    return {"cleared": True}

@app.post("/tools/rclone")
def run_rclone(req: ToolRequest):
    return migrator_tools.rclone_sync(req.src, req.dst, prefix=req.prefix)

@app.post("/tools/s5cmd")
def run_s5cmd(req: ToolRequest):
    return migrator_tools.s5cmd_copy(req.src, req.dst, prefix=req.prefix)

@app.get("/chaos/status")
def chaos_status():
    return {"failed_endpoints": chaos.get_failed_endpoints()}

@app.post("/chaos/fail/{name}")
def chaos_fail(name: str):
    return {"failed_endpoints": chaos.fail_endpoint(name)}

@app.post("/chaos/recover/{name}")
def chaos_recover(name: str):
    return {"failed_endpoints": chaos.recover_endpoint(name)}

@app.post("/chaos/clear")
def chaos_clear():
    return {"failed_endpoints": chaos.clear_failures()}
