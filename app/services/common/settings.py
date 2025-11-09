from __future__ import annotations

from typing import Any, Optional, List

from .db import SessionLocal, SystemSetting


def _get_setting_row(key: str) -> Optional[SystemSetting]:
    with SessionLocal() as session:
        return session.query(SystemSetting).filter_by(key=key).first()


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    row = _get_setting_row(key)
    return row.value if row else default


def get_bool(key: str, default: Optional[bool] = None) -> Optional[bool]:
    val = get_setting(key)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def get_list(key: str) -> List[str]:
    val = get_setting(key)
    if not val:
        return []
    return [item.strip() for item in val.split(",") if item.strip()]


def set_setting(key: str, value: Any) -> None:
    with SessionLocal() as session:
        row = session.query(SystemSetting).filter_by(key=key).first()
        if row:
            row.value = str(value)
        else:
            session.add(SystemSetting(key=key, value=str(value)))
        session.commit()
