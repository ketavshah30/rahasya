"""Per-scan network-call budget scoreboard (FIXES_NEW.md §J.2).

Each `BaseModule.safe_execute` invocation increments the counter for the
current scan, and refuses to run once the configured cap is reached. The
orchestrator seeds the cap on scan start and clears the entry on scan end.

Kept intentionally separate from the module registry / orchestrator import
graph to avoid cycles (module base → orchestrator would be circular).
"""

from threading import Lock
from typing import Dict, Optional

_LOCK = Lock()
_CALLS: Dict[str, int] = {}
_CAPS: Dict[str, int] = {}
_TRIPPED: Dict[str, bool] = {}


def register_scan(scan_id: str, cap: int) -> None:
    if not scan_id or cap <= 0:
        return
    with _LOCK:
        _CALLS[scan_id] = 0
        _CAPS[scan_id] = cap
        _TRIPPED[scan_id] = False


def try_charge(scan_id: str) -> bool:
    """Charge one network-call worth of budget. Returns False if exceeded."""
    if not scan_id:
        return True
    with _LOCK:
        if _TRIPPED.get(scan_id):
            return False
        cap = _CAPS.get(scan_id)
        if cap is None:
            return True
        current = _CALLS.get(scan_id, 0)
        if current >= cap:
            _TRIPPED[scan_id] = True
            return False
        _CALLS[scan_id] = current + 1
        return True


def calls_used(scan_id: str) -> int:
    with _LOCK:
        return _CALLS.get(scan_id, 0)


def is_tripped(scan_id: str) -> bool:
    with _LOCK:
        return bool(_TRIPPED.get(scan_id))


def clear_scan(scan_id: str) -> None:
    with _LOCK:
        _CALLS.pop(scan_id, None)
        _CAPS.pop(scan_id, None)
        _TRIPPED.pop(scan_id, None)


def snapshot(scan_id: str) -> Optional[Dict[str, int]]:
    with _LOCK:
        if scan_id not in _CAPS:
            return None
        return {"calls": _CALLS.get(scan_id, 0), "cap": _CAPS[scan_id]}
