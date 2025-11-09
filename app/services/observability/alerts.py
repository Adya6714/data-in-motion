from __future__ import annotations

from typing import Any, Dict, List

from ..common.db import SessionLocal, AlertEvent


def _dedup_exists(alert_type: str, message: str) -> bool:
    with SessionLocal() as session:
        return (
            session.query(AlertEvent)
            .filter_by(type=alert_type, message=message, acknowledged=False)
            .first()
            is not None
        )


def create_alert(alert_type: str, severity: str, message: str, data: Dict[str, Any] | None = None, dedup: bool = True) -> None:
    if dedup and _dedup_exists(alert_type, message):
        return
    with SessionLocal() as session:
        session.add(
            AlertEvent(
                type=alert_type,
                severity=severity,
                message=message,
                data=data or {},
            )
        )
        session.commit()


def list_alerts(include_ack: bool = False) -> List[Dict[str, Any]]:
    with SessionLocal() as session:
        query = session.query(AlertEvent)
        if not include_ack:
            query = query.filter_by(acknowledged=False)
        query = query.order_by(AlertEvent.created_at.desc())
        return [
            {
                "id": row.id,
                "type": row.type,
                "severity": row.severity,
                "message": row.message,
                "data": row.data,
                "acknowledged": row.acknowledged,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in query.all()
        ]


def acknowledge(alert_id: int) -> bool:
    with SessionLocal() as session:
        row = session.query(AlertEvent).filter_by(id=alert_id).first()
        if not row:
            return False
        row.acknowledged = True
        session.commit()
        return True


def clear_alerts():
    with SessionLocal() as session:
        session.query(AlertEvent).delete()
        session.commit()
