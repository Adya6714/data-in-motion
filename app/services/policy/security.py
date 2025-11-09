from __future__ import annotations

from ..common import settings
from ..common.config import ENCRYPTION_ENABLED, S3_ENDPOINTS

_SETTINGS_KEY = "enforce_encryption"


def is_encryption_enforced() -> bool:
    enforced = settings.get_bool(_SETTINGS_KEY)
    if enforced is None:
        return ENCRYPTION_ENABLED
    return enforced


def set_enforcement(flag: bool) -> bool:
    settings.set_setting(_SETTINGS_KEY, "true" if flag else "false")
    return flag


def endpoint_is_encrypted(name: str) -> bool:
    for endpoint in S3_ENDPOINTS:
        if endpoint.name == name:
            return getattr(endpoint, "encrypted", True)
    return True
