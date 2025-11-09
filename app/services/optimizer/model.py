from __future__ import annotations
from dataclasses import dataclass
from typing import List
import logging
import time
import numpy as np
from sklearn.linear_model import LogisticRegression
from ..optimizer.placement_milp import Site, solve_placement
from ..optimizer.scoring import score_location, Weights
from ..common.db import SessionLocal, FileMeta
from ..common.config import (
    S3_ENDPOINTS,
    SLA_LATENCY_MS,
    REPLICATION_FACTOR,
    HOT_DEMOTE_RECENCY_S,
    HOT_DEMOTE_P_THRESHOLD,
    WARM_DEMOTE_RECENCY_S,
    HEAT_WARM_THRESHOLD,
    HEAT_HOT_THRESHOLD,
    HEAT_PRED_BOOST,
)
from ..policy import security
from app.ml import serve_tiers

log = logging.getLogger("optimizer")
_model = LogisticRegression()

@dataclass
class Placement:
    primary: str
    replicas: List[str]
    tier: str
    primary_latency_ms: float = 0.0
    primary_cost_gb: float = 0.0

DEFAULT_SITES: List[Site] = [
    Site(name="azure", p95_ms=70, cost_gb=0.020, provider="azure"),
    Site(name="aws",   p95_ms=50, cost_gb=0.023, provider="aws"),
    Site(name="gcp",   p95_ms=60, cost_gb=0.026, provider="gcp"),
]

def get_candidate_sites() -> List[Site]:
    try:
        sites = [
            Site(
                name=e.name,
                p95_ms=getattr(e, "latency_ms", 60),
                cost_gb=getattr(e, "cost_per_gb", 0.02),
                provider=e.name,
                encrypted=getattr(e, "encrypted", True),
            )
            for e in (S3_ENDPOINTS or [])
        ]
        return sites if sites else DEFAULT_SITES.copy()
    except Exception:
        return DEFAULT_SITES.copy()

def _predict_hot_prob_fallback(access_1h: int, access_24h: int, size_bytes: int) -> float:
    gb = max(1.0, size_bytes / 1e9)
    hits_per_gb_1h = access_1h / gb
    if hits_per_gb_1h >= 50 or access_1h >= 40:
        return 0.90
    if hits_per_gb_1h >= 10 or access_1h >= 10:
        return 0.45
    if access_24h <= 5:
        return 0.05
    return 0.20

def predict_hot_prob(access_1h: int, access_24h: int, size_bytes: int) -> float:
    try:
        if getattr(serve_tiers, "_model", None) is not None:
            return float(
                serve_tiers.predict_proba(
                    {
                        "access_1h": access_1h,
                        "access_24h": access_24h,
                        "size_bytes": size_bytes,
                        "recency_s": 0.0,
                        "hour_of_day": 0,
                        "day_of_week": 0,
                    }
                )
            )
    except Exception:
        pass
    try:
        if hasattr(_model, "coef_"):
            x = np.array([access_1h, access_24h, size_bytes], dtype=float)
            return float(_model.predict_proba([x])[0][1])
    except Exception:
        pass
    return _predict_hot_prob_fallback(access_1h, access_24h, size_bytes)

MIN_RESIDENCY = {"hot": 30 * 60, "warm": 2 * 60 * 60, "cold": 24 * 60 * 60}
HOT_CAP_RATIO = 0.30

def _get_hot_ratio() -> float:
    try:
        with SessionLocal() as s:
            total = s.query(FileMeta).count()
            if total == 0:
                return 0.0
            hot = s.query(FileMeta).filter_by(tier="hot").count()
            return hot / total
    except Exception:
        return 0.0

def _next_tier(
    heat: float,
    p_adj: float,
    access_1h: int,
    access_24h: int,
    last_tier: str,
    last_move_ts: float,
    recency_s: float,
) -> str:
    now = time.time()
    age = now - (last_move_ts or 0)

    boosted_heat = heat + HEAT_PRED_BOOST * max(0.0, min(1.0, p_adj))
    if boosted_heat >= HEAT_HOT_THRESHOLD:
        desired = "hot"
    elif boosted_heat >= HEAT_WARM_THRESHOLD:
        desired = "warm"
    else:
        desired = "cold"

    if desired == "hot" and last_tier != "hot" and _get_hot_ratio() >= HOT_CAP_RATIO:
        desired = "warm"

    if last_tier == "hot" and desired != "hot":
        if recency_s < HOT_DEMOTE_RECENCY_S or boosted_heat >= HEAT_HOT_THRESHOLD * 0.8 or age < MIN_RESIDENCY["hot"]:
            return "hot"
        if p_adj <= HOT_DEMOTE_P_THRESHOLD or access_24h < 15:
            return "warm"
        return "hot"

    if last_tier == "warm" and desired == "cold":
        if age < MIN_RESIDENCY["warm"] or recency_s < WARM_DEMOTE_RECENCY_S:
            return "warm"

    if last_tier in ("cold", "warm") and desired == "hot":
        if _get_hot_ratio() < HOT_CAP_RATIO and (access_1h >= 30 or p_adj >= 0.6):
            return "hot"
        return "warm"

    return desired

def optimize_placement(
    key: str,
    size_bytes: int,
    access_1h: int,
    access_24h: int,
    *,
    last_tier: str | None = None,
    last_move_ts: float | None = None,
    current_primary: str | None = None,
    recency_s: float | None = None,
    heat_score: float | None = None,
) -> Placement:
    override = last_tier is not None or current_primary is not None or heat_score is not None
    if not override:
        last_tier, current_primary = "warm", None
        last_move_ts = 0.0
        recency_s = 0.0
        heat_score = 0.0
        try:
            with SessionLocal() as s:
                fm = s.query(FileMeta).filter_by(key=key).first()
                if fm:
                    last_tier = fm.tier or "warm"
                    try:
                        last_move_ts = float(getattr(fm, "last_move_ts", 0) or 0)
                    except Exception:
                        last_move_ts = 0.0
                    current_primary = (fm.location_primary or None)
                    try:
                        if fm.last_access_ts is not None:
                            recency_s = max(0.0, time.time() - fm.last_access_ts.timestamp())
                    except Exception:
                        recency_s = 0.0
                    try:
                        heat_score = float(getattr(fm, "heat_score", 0.0) or 0.0)
                    except Exception:
                        heat_score = 0.0
        except Exception:
            pass
    else:
        last_tier = last_tier or "warm"
        last_move_ts = float(last_move_ts or 0.0)
        recency_s = float(recency_s or 0.0)
        heat_score = float(heat_score or 0.0)

    p = predict_hot_prob(access_1h, access_24h, size_bytes)
    sites = get_candidate_sites()
    if security.is_encryption_enforced():
        secured_sites = [s for s in sites if getattr(s, "encrypted", True)]
        if secured_sites:
            sites = secured_sites
    if not sites:
        if heat_score >= HEAT_HOT_THRESHOLD:
            tier = "hot"
        elif heat_score >= HEAT_WARM_THRESHOLD:
            tier = "warm"
        else:
            tier = "hot" if p >= 0.60 else ("warm" if p >= 0.25 else "cold")
        try:
            with SessionLocal() as s:
                fm = s.query(FileMeta).filter_by(key=key).first()
                if fm:
                    fm.tier_recommended = tier
                    s.commit()
                    current = [fm.location_primary] + [rep for rep in (fm.location_replicas or "").split(",") if rep]
                    if current:
                        return Placement(primary=current[0], replicas=current[1:], tier=tier)
        except Exception:
            pass
        return Placement(primary="default", replicas=[], tier=tier)

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
        "egress_penalty": {s.name: (0.0 if (current_primary and s.name == current_primary) else 1.0) for s in sites},
    }
    scores = [score_location(feat_ctx, s, w) for s in sites]

    max_score = max(scores) if scores else 0.0
    site_quality_factor = max(0.6, min(1.0, max_score / 3.0))
    p_adj = p * (0.6 + 0.4 * site_quality_factor)
    tier = _next_tier(
        heat_score,
        p_adj,
        access_1h,
        access_24h,
        last_tier=last_tier,
        last_move_ts=last_move_ts,
        recency_s=recency_s,
    )

    if len(sites) == 1:
        chosen = [sites[0].name]
    else:
        score_weight = 0.2 + 0.6 * float(max(0.0, min(1.0, p)))
        chosen, _ = solve_placement(
            sites,
            rf=REPLICATION_FACTOR,
            sla_ms=SLA_LATENCY_MS,
            avoid_provider_clash=True,
            site_scores=scores,
            score_weight=score_weight,
        )
        if not chosen:
            cheapest = sorted(sites, key=lambda s: (s.cost_gb, s.p95_ms))[0].name
            chosen = [cheapest]

    name_to_site = {s.name: s for s in sites}
    chosen_sites = [name_to_site[n] for n in chosen if n in name_to_site]
    chosen_sites_sorted = sorted(chosen_sites, key=lambda s: (s.p95_ms, s.cost_gb))
    if chosen_sites_sorted:
        primary_site = chosen_sites_sorted[0]
        primary = primary_site.name
        primary_latency = float(primary_site.p95_ms)
        primary_cost = float(primary_site.cost_gb)
        replicas = [s.name for s in chosen_sites_sorted[1:]]
    else:
        primary_latency = float(sites[0].p95_ms) if sites else 0.0
        primary_cost = float(sites[0].cost_gb) if sites else 0.0
        primary, replicas = (sites[0].name if sites else "default"), []

    try:
        with SessionLocal() as s:
            fm = s.query(FileMeta).filter_by(key=key).first()
            if fm:
                fm.tier_recommended = tier
                s.commit()
    except Exception:
        pass

    log.info(
        "decide key=%s p=%.2f p_adj=%.2f last=%s chosen=%s tier=%s scores=%s",
        key,
        p,
        p_adj,
        last_tier,
        [primary] + replicas,
        tier,
        [round(x, 3) for x in scores],
    )
    return Placement(
        primary=primary,
        replicas=replicas,
        tier=tier,
        primary_latency_ms=primary_latency,
        primary_cost_gb=primary_cost,
    )
