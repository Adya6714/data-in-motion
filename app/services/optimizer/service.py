import logging
import math
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from ..common.db import SessionLocal, FileMeta, MigrationTask
from ..common.config import HEAT_TAU_SEC, HEAT_DAILY_WEIGHT, SLA_LATENCY_MS, HEAT_HOT_THRESHOLD
from .model import optimize_placement
from ..observability.metrics import (
    placement_evaluations_total,
    placement_evaluation_duration,
    placement_heat_gauge,
)
from ..observability import alerts

log = logging.getLogger("optimizer-service")
COST_ALERT_THRESHOLD = 0.024


def _as_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                except ValueError:
                    return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def _update_heat_score(fm: FileMeta, now: datetime, prev_last_ts: datetime | None) -> float:
    tau = max(1.0, HEAT_TAU_SEC)
    prev_heat = float(getattr(fm, "heat_score", 0.0) or 0.0)
    anchor = prev_last_ts or now
    delta = max(0.0, (now - anchor).total_seconds())
    decay = math.exp(-delta / tau) if tau > 0 else 0.0
    hourly = float(fm.access_1h or 0)
    daily_extra = max(0.0, float(fm.access_24h or 0) - hourly)
    new_heat = prev_heat * decay + hourly + HEAT_DAILY_WEIGHT * daily_extra
    fm.heat_score = new_heat
    return new_heat


def evaluate_and_queue(key: str) -> None:
    start_ts = time.perf_counter()
    now = datetime.now(timezone.utc)
    h1 = now - timedelta(hours=1)
    h24 = now - timedelta(hours=24)

    with SessionLocal() as s:
        try:
            dialect = s.bind.dialect.name if s.bind is not None else "sqlite"
        except Exception:
            dialect = "sqlite"

        query = s.query(FileMeta).filter_by(key=key)
        if dialect != "sqlite":
            query = query.with_for_update()
        fm: FileMeta | None = query.first()
        if not fm:
            log.debug("evaluate_by_key skipped missing key=%s", key)
            return

        old_tier = fm.tier

        if dialect == "sqlite":
            row = s.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN ts >= datetime('now','-1 hour') THEN 1 ELSE 0 END) AS c1,
                      SUM(CASE WHEN ts >= datetime('now','-24 hour') THEN 1 ELSE 0 END) AS c24,
                      MAX(ts) AS last_ts
                    FROM access_event
                    WHERE key = :k
                    """
                ),
                {"k": key},
            ).first()
        else:
            row = s.execute(
                text(
                    """
                    SELECT
                      SUM(CASE WHEN ts >= :cut1  THEN 1 ELSE 0 END) AS c1,
                      SUM(CASE WHEN ts >= :cut24 THEN 1 ELSE 0 END) AS c24,
                      MAX(ts) AS last_ts
                    FROM access_event
                    WHERE key = :k
                    """
                ),
                {"k": key, "cut1": h1, "cut24": h24},
            ).first()

        fm.access_1h = int(row.c1 or 0)
        fm.access_24h = int(row.c24 or 0)

        prev_last_ts = _as_datetime(fm.last_access_ts)
        new_last_ts = _as_datetime(row.last_ts) if row.last_ts is not None else None
        if new_last_ts is not None:
            fm.last_access_ts = new_last_ts
        heat = _update_heat_score(fm, now, prev_last_ts)

        old_primary = fm.location_primary
        old_replicas = (
            [p for p in (fm.location_replicas or "").split(",") if p]
        )

        if new_last_ts is not None:
            recency_override = max(0.0, (now - new_last_ts).total_seconds())
        elif prev_last_ts is not None:
            recency_override = max(0.0, (now - prev_last_ts).total_seconds())
        else:
            recency_override = 0.0

        placement = optimize_placement(
            fm.key,
            fm.size_bytes,
            fm.access_1h,
            fm.access_24h,
            last_tier=fm.tier,
            current_primary=fm.location_primary,
            recency_s=recency_override,
            heat_score=heat,
        )

        fm.tier = placement.tier
        fm.location_primary = placement.primary
        fm.location_replicas = ",".join(placement.replicas)

        new_locations = [placement.primary] + placement.replicas
        old_locations = [old_primary] + old_replicas

        for dst in new_locations:
            if dst not in old_locations:
                s.add(
                    MigrationTask(
                        key=fm.key,
                        src=old_primary,
                        dst=dst,
                        status="queued",
                        error=None,
                    )
                )

        for dst in old_locations:
            if dst not in new_locations:
                s.add(
                    MigrationTask(
                        key=fm.key,
                        src=dst,
                        dst=placement.primary,
                        status="cleanup",
                        error=None,
                    )
                )

        s.commit()

        log.info(
            "evaluate key=%s access_1h=%s access_24h=%s heat=%.2f tier %s->%s primary %s->%s queued=%d cleanup=%d",
            key,
            fm.access_1h,
            fm.access_24h,
            heat,
            old_tier,
            placement.tier,
            old_primary,
            placement.primary,
            sum(1 for dst in new_locations if dst not in old_locations),
            sum(1 for dst in old_locations if dst not in new_locations),
        )

        duration = time.perf_counter() - start_ts
        placement_evaluation_duration.observe(duration)
        placement_evaluations_total.labels(result_tier=placement.tier).inc()
        placement_heat_gauge.labels(key=fm.key).set(heat)

        alert_payload = {
            "key": key,
            "tier": placement.tier,
            "primary": placement.primary,
            "latency_ms": placement.primary_latency_ms,
            "cost_gb": placement.primary_cost_gb,
            "heat": round(heat, 2),
        }
        if placement.primary_latency_ms > SLA_LATENCY_MS:
            alerts.create_alert(
                "latency_sla",
                "warning",
                f"{key} on {placement.primary} exceeds SLA ({placement.primary_latency_ms:.1f} ms)",
                alert_payload,
            )
        if placement.primary_cost_gb > COST_ALERT_THRESHOLD and heat > HEAT_HOT_THRESHOLD:
            alerts.create_alert(
                "cost_spike",
                "warning",
                f"{key} hot on expensive tier ({placement.primary_cost_gb:.3f}/GB)",
                alert_payload,
            )
        if fm.access_1h >= 500:
            alerts.create_alert(
                "traffic_spike",
                "info",
                f"{key} saw {fm.access_1h} hits in the last hour",
                {"key": key, "access_1h": fm.access_1h},
                dedup=False,
            )
