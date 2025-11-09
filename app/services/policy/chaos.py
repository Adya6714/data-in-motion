from __future__ import annotations

from typing import List

from ..common import settings

FAIL_KEY = "chaos_fail_endpoints"


def get_failed_endpoints() -> List[str]:
    return settings.get_list(FAIL_KEY)


def fail_endpoint(name: str) -> List[str]:
    current = set(get_failed_endpoints())
    current.add(name)
    settings.set_setting(FAIL_KEY, ",".join(sorted(current)))
    return sorted(current)


def recover_endpoint(name: str) -> List[str]:
    current = set(get_failed_endpoints())
    if name in current:
        current.remove(name)
    settings.set_setting(FAIL_KEY, ",".join(sorted(current)))
    return sorted(current)


def clear_failures() -> List[str]:
    settings.set_setting(FAIL_KEY, "")
    return []
