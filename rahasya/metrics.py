"""Optional Prometheus metrics for workers and orchestration."""

from __future__ import annotations

import os
from threading import Lock

try:
    from prometheus_client import Counter, Gauge, start_http_server
except ImportError:  # pragma: no cover - optional production dependency
    Counter = Gauge = None
    start_http_server = None


SCANS_STARTED = Counter("rahasya_scans_started_total", "Scans dispatched") if Counter else None
SCANS_COMPLETED = Counter("rahasya_scans_completed_total", "Scans completed", ["status"]) if Counter else None
ACTIVE_SCANS = Gauge("rahasya_active_scans", "Currently active scans") if Gauge else None
_START_LOCK = Lock()
_STARTED = False


def start_metrics_server() -> bool:
    global _STARTED
    port = os.getenv("RAHASYA_METRICS_PORT")
    if not port or start_http_server is None:
        return False
    with _START_LOCK:
        if not _STARTED:
            start_http_server(int(port))
            _STARTED = True
    return True
