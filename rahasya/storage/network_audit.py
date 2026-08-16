"""Per-scan module and network audit trail with report generation."""

from __future__ import annotations

import csv
import html
import json
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rahasya.config import settings


_scan_id: ContextVar[Optional[str]] = ContextVar("rahasya_audit_scan_id", default=None)
_source_module: ContextVar[str] = ContextVar("rahasya_audit_source_module", default="system")
_audit_root: ContextVar[Optional[str]] = ContextVar("rahasya_audit_root", default=None)
_write_lock = threading.RLock()

SECRET_QUERY_KEYS = {
    "key", "api_key", "apikey", "token", "access_token", "password", "passwd",
    "secret", "client_secret", "authorization", "auth",
}
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_url(url: Optional[str]) -> Optional[str]:
    """Keep destinations useful while removing credentials and secret query values."""
    if not url:
        return url
    try:
        parts = urlsplit(str(url))
        hostname = parts.hostname or ""
        if parts.port:
            hostname = f"{hostname}:{parts.port}"
        if parts.username:
            hostname = f"{parts.username}:REDACTED@{hostname}"
        query = urlencode([
            (key, "REDACTED" if key.casefold() in SECRET_QUERY_KEYS else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ])
        return urlunsplit((parts.scheme, hostname or parts.netloc, parts.path, query, parts.fragment))
    except (TypeError, ValueError):
        return str(url)


def redact_text_urls(value: Any) -> Any:
    """Redact secrets in URLs embedded in error messages while preserving diagnostics."""
    if not isinstance(value, str):
        return value
    return URL_PATTERN.sub(lambda match: redact_url(match.group(0)) or "", value)


@contextmanager
def audit_scope(scan_id: str, source_module: str, root: Optional[Path | str] = None) -> Iterator[None]:
    """Attach scan/module identity to nested async HTTP calls via contextvars."""
    scan_token = _scan_id.set(scan_id)
    module_token = _source_module.set(source_module)
    root_token = _audit_root.set(str(root) if root is not None else None)
    try:
        yield
    finally:
        _audit_root.reset(root_token)
        _source_module.reset(module_token)
        _scan_id.reset(scan_token)


class NetworkAuditStore:
    """Append-only JSON Lines audit log stored beside scan snapshots."""

    def __init__(self, root: Optional[Path | str] = None):
        self.root = Path(root or settings.storage.scan_dir)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, scan_id: str) -> Path:
        safe = "".join(char for char in str(scan_id) if char.isalnum() or char in "-_")
        if not safe or safe != str(scan_id):
            raise ValueError("Invalid scan id")
        return self.root / f"{safe}.network.jsonl"

    def record(self, scan_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scan_id": scan_id,
            **{key: value for key, value in event.items() if value is not None},
        }
        path = self.path(scan_id)
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        with _write_lock:
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        return payload

    def load(self, scan_id: str) -> List[Dict[str, Any]]:
        path = self.path(scan_id)
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def delete(self, scan_id: str) -> bool:
        path = self.path(scan_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def summary(self, scan_id: str) -> Dict[str, Any]:
        return summarize_events(self.load(scan_id))


def summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build dashboard/report totals from an already-loaded event collection."""
    network = [event for event in events if event.get("event_type") == "network_request"]
    modules = [event for event in events if str(event.get("event_type", "")).startswith("module_")]
    failure_outcomes = {"failed", "error", "http_error", "rate_limited", "timeout", "cancelled"}
    failed = [event for event in network if event.get("outcome") in failure_outcomes]
    return {
        "total_events": len(events),
        "network_attempts": len(network),
        "successful_requests": sum(event.get("outcome") == "success" for event in network),
        "failed_requests": len(failed),
        "unique_hosts": len({event.get("host") for event in network if event.get("host")}),
        "module_events": len(modules),
        "modules_seen": sorted({event.get("source_module") for event in events if event.get("source_module")}),
        "hosts_seen": sorted({event.get("host") for event in network if event.get("host")}),
    }


def record_audit_event(
    event_type: str,
    *,
    outcome: str,
    scan_id: Optional[str] = None,
    source_module: Optional[str] = None,
    root: Optional[Path | str] = None,
    url: Optional[str] = None,
    **details: Any,
) -> Optional[Dict[str, Any]]:
    """Record an event using explicit values or the active module audit scope."""
    resolved_scan_id = scan_id or _scan_id.get()
    if not resolved_scan_id:
        return None
    resolved_root = root or _audit_root.get() or settings.storage.scan_dir
    safe_url = redact_url(url)
    host = None
    if safe_url:
        try:
            host = urlsplit(safe_url).hostname
        except ValueError:
            host = None
    event = {
        "event_type": event_type,
        "outcome": outcome,
        "source_module": source_module or _source_module.get(),
        "url": safe_url,
        "host": host,
        **{key: redact_text_urls(value) for key, value in details.items()},
    }
    return NetworkAuditStore(resolved_root).record(resolved_scan_id, event)


AUDIT_COLUMNS = [
    "timestamp", "event_type", "outcome", "source_module", "method", "url", "host",
    "status_code", "duration_ms", "attempt", "max_attempts", "via_proxy", "purpose",
    "provider", "source_name", "site", "provider_status", "return_code", "skip_reason",
    "result_count", "error_type", "error", "entity_type", "entity_value", "message",
]


def audit_csv(events: List[Dict[str, Any]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=AUDIT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(events)
    return buffer.getvalue()


def audit_json(events: List[Dict[str, Any]]) -> str:
    return json.dumps(events, indent=2, ensure_ascii=False, default=str)


def audit_html_report(scan_id: str, events: List[Dict[str, Any]]) -> str:
    summary = summarize_events(events)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(event.get('timestamp', '')))}</td>"
        f"<td>{html.escape(str(event.get('source_module', '')))}</td>"
        f"<td>{html.escape(str(event.get('event_type', '')))}</td>"
        f"<td>{html.escape(str(event.get('outcome', '')))}</td>"
        f"<td>{html.escape(str(event.get('method', '')))}</td>"
        f"<td>{html.escape(str(event.get('status_code', '')))}</td>"
        f"<td>{html.escape(str(event.get('site', event.get('host', ''))))}</td>"
        f"<td class='url'>{html.escape(str(event.get('url', '')))}</td>"
        f"<td>{html.escape(str(event.get('duration_ms', '')))}</td>"
        f"<td>{html.escape(str(event.get('error', event.get('message', ''))))}</td>"
        "</tr>"
        for event in events
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Rahasya Network Audit {html.escape(scan_id)}</title>
<style>body{{font:13px Consolas,monospace;background:#031008;color:#d8ffe9;padding:24px}}table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #17633f;padding:7px;text-align:left;vertical-align:top}}th{{background:#0b2c1c;color:#00ff88;position:sticky;top:0}}
.metrics{{display:flex;gap:12px;flex-wrap:wrap}}.metric{{border:1px solid #00a85a;padding:10px}}.url{{word-break:break-all;max-width:440px}}</style></head>
<body><h1>Rahasya Network & Source Audit</h1><p>Scan: {html.escape(scan_id)}</p><div class="metrics">
<div class="metric">Network attempts: {summary['network_attempts']}</div><div class="metric">Successful: {summary['successful_requests']}</div>
<div class="metric">Failed: {summary['failed_requests']}</div><div class="metric">Unique hosts: {summary['unique_hosts']}</div>
<div class="metric">Module events: {summary['module_events']}</div></div><h2>Chronological event log</h2>
<table><thead><tr><th>Timestamp</th><th>Source</th><th>Event</th><th>Outcome</th><th>Method</th><th>Status</th><th>Portal/host</th><th>URL</th><th>ms</th><th>Error/message</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
